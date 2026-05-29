#!/usr/bin/env python3
"""Automate a Codabench submission upload and extract the results.

This mirrors, over the REST API, exactly what the codabench.org web UI does when
you drop a submission zip onto a competition phase:

    1. POST /api/api-token-auth/         username+password  -> {"token": ...}   (or use CODABENCH_TOKEN)
    2. GET  /api/competitions/<id>/      find the phase (+ its task ids)
    3. POST /api/datasets/               register the file   -> {"key", "sassy_url"}
    4. PUT  <sassy_url>                  upload the zip bytes to object storage
    5. PUT  /api/datasets/completed/<key>/
    6. POST /api/submissions/            create submission   -> {"id", ...}
    7. GET  /api/submissions/<id>/       poll until Finished / Failed / Cancelled
    8. GET  /api/submissions/<id>/get_details/   -> prediction_result + scoring_result URLs
    9. download + unzip + pretty-print scores.json and metrics.json

Auth (pick one). Values may live in a .env file at the repo root (see .env.example):
    * CODABENCH_TOKEN=<drf token>                          (skips step 1)
    * CODABENCH_USERNAME / CODABENCH_PASSWORD              (or --username/--password)

Examples
--------
    # List the phases of the competition (handy on first run):
    python tools/codabench_submit.py --list-phases

    # Build a zip from submission.py, upload, wait, and print scores:
    export CODABENCH_USERNAME=you@example.com CODABENCH_PASSWORD=...
    python tools/codabench_submit.py submissions/finance_band/submission.py

    # Re-fetch results for an already-finished submission:
    python tools/codabench_submit.py --submission-id 123456
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import warnings
import zipfile
from datetime import datetime
from pathlib import Path

# The conda interpreter ships a urllib3 newer than requests' vendored support
# for; the resulting RequestsDependencyWarning is noisy and harmless here. Filter
# it before importing requests, since it is raised at import time.
warnings.filterwarnings("ignore", category=Warning, module="requests")

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs `requests`. Install it: pip install requests")

DEFAULT_BASE_URL = "https://www.codabench.org"
DEFAULT_COMPETITION = 16778
DONE_STATES = {"Finished", "Failed", "Cancelled", "Unknown"}


def load_dotenv(*paths: Path) -> None:
    """Minimal .env loader (no third-party dep).

    Reads simple KEY=VALUE lines (optionally `export KEY=VALUE`, with #-comments
    and surrounding single/double quotes) and sets them in os.environ. Existing
    environment variables always win, so a real env var overrides the file.
    """
    seen = set()
    for path in paths:
        if not path or path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


def default_env_files() -> list[Path]:
    """`.env` next to this script's project root and in the current directory."""
    repo_root = Path(__file__).resolve().parent.parent
    custom = os.environ.get("CODABENCH_ENV_FILE")
    files = [Path(custom)] if custom else []
    files += [repo_root / ".env", Path.cwd() / ".env"]
    return files


class CodabenchError(RuntimeError):
    pass


class CodabenchClient:
    """Thin wrapper over the Codabench REST API for participant submissions."""

    def __init__(self, base_url: str, token: str, verbose: bool = True):
        self.base = base_url.rstrip("/")
        self.api = f"{self.base}/api/"
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Token {token}"})

    # -- low level ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else self.api + path.lstrip("/")

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        resp = self.session.request(method, self._url(path), **kw)
        if resp.status_code >= 400:
            body = resp.text
            try:
                body = json.dumps(resp.json(), indent=2)
            except ValueError:
                pass
            raise CodabenchError(
                f"{method} {self._url(path)} -> {resp.status_code}\n{body}"
            )
        return resp

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    # -- auth --------------------------------------------------------------
    @classmethod
    def login(
        cls,
        base_url: str,
        username: str,
        password: str,
        verbose: bool = True,
    ) -> "CodabenchClient":
        url = f"{base_url.rstrip('/')}/api/api-token-auth/"
        resp = requests.post(url, data={"username": username, "password": password})
        if resp.status_code >= 400:
            raise CodabenchError(
                f"Login failed ({resp.status_code}). Check username/password.\n{resp.text}"
            )
        token = resp.json().get("token")
        if not token:
            raise CodabenchError(f"Login response had no token: {resp.text}")
        if verbose:
            print(f"Authenticated as {username}", flush=True)
        return cls(base_url, token, verbose=verbose)

    # -- competition / phases ---------------------------------------------
    def get_competition(self, competition_id: int) -> dict:
        return self._request("GET", f"competitions/{competition_id}/").json()

    def select_phase(self, competition: dict, *, phase_id=None, phase_index=None,
                     phase_name=None) -> dict:
        phases = competition.get("phases", [])
        if not phases:
            raise CodabenchError("Competition has no phases.")

        if phase_id is not None:
            match = [p for p in phases if p.get("id") == phase_id]
        elif phase_index is not None:
            match = [p for p in phases if p.get("index") == phase_index]
        elif phase_name is not None:
            match = [p for p in phases if p.get("name") == phase_name]
        else:
            current = [p for p in phases if p.get("status") == "Current"]
            match = current or (phases if len(phases) == 1 else [])

        if len(match) != 1:
            listing = "\n".join(
                f"  index={p.get('index')} id={p.get('id')} "
                f"status={p.get('status')!r} name={p.get('name')!r}"
                for p in phases
            )
            raise CodabenchError(
                "Could not unambiguously pick a phase. Use --phase-id / --phase-index / "
                f"--phase-name. Available phases:\n{listing}"
            )
        return match[0]

    @staticmethod
    def phase_task_names(phase: dict) -> list:
        """Human-readable task names/keys for a phase, for display only."""
        names = []
        for task in phase.get("tasks", []) or []:
            if isinstance(task, dict):
                names.append(task.get("name") or task.get("key") or task.get("id"))
            else:
                names.append(task)
        return names

    # -- upload ------------------------------------------------------------
    def upload_submission_file(self, zip_path: Path, competition_id: int) -> str:
        size = zip_path.stat().st_size
        meta = {
            "type": "submission",
            "competition": competition_id,
            "request_sassy_file_name": zip_path.name,
            "file_name": zip_path.name,
            "file_size": size,
        }
        self._log(f"Registering dataset for {zip_path.name} ({size} bytes)...")
        data = self._request("POST", "datasets/", json=meta).json()
        key, sassy_url = data["key"], data["sassy_url"]

        self._log("Uploading file to storage...")
        # Presigned storage URL: send raw bytes, no Authorization header.
        with open(zip_path, "rb") as fh:
            put = requests.put(
                sassy_url, data=fh, headers={"Content-Type": "application/zip"}
            )
        if put.status_code >= 400:
            raise CodabenchError(
                f"Storage upload failed ({put.status_code}): {put.text[:500]}"
            )

        self._log("Finalizing upload...")
        self._request("PUT", f"datasets/completed/{key}/")
        return key

    # -- submission --------------------------------------------------------
    def create_submission(self, data_key: str, phase_id: int,
                          task_ids: list | None = None) -> dict:
        payload = {
            "data": data_key,
            "phase": phase_id,
            "fact_sheet_answers": None,
            "organization": None,
        }
        if task_ids:
            payload["tasks"] = task_ids
        self._log("Creating submission...")
        return self._request("POST", "submissions/", json=payload).json()

    def get_submission(self, submission_id: int) -> dict:
        return self._request("GET", f"submissions/{submission_id}/").json()

    def get_submission_details(self, submission_id: int) -> dict:
        return self._request(
            "GET", f"submissions/{submission_id}/get_details/"
        ).json()

    def poll(self, submission_id: int, *, interval: float, timeout: float) -> dict:
        deadline = time.time() + timeout
        last = None
        while True:
            sub = self.get_submission(submission_id)
            status = sub.get("status")
            if status != last:
                self._log(f"  [{datetime.now():%H:%M:%S}] status: {status}")
                last = status
            if status in DONE_STATES:
                return sub
            if time.time() > deadline:
                raise CodabenchError(
                    f"Timed out after {timeout:.0f}s waiting for submission "
                    f"{submission_id} (last status: {status})."
                )
            time.sleep(interval)


# ---------------------------------------------------------------------------
# Helpers: build zip, download + parse results
# ---------------------------------------------------------------------------
def build_submission_zip(source: Path, out_dir: Path) -> Path:
    """Return a zip with submission.py at the root.

    `source` may be: an existing .zip (validated), a submission.py file, or a
    directory containing submission.py.
    """
    if source.is_file() and source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            names = zf.namelist()
        if "submission.py" not in names:
            raise CodabenchError(
                f"{source} does not contain submission.py at its root. "
                f"Contents: {names}"
            )
        return source

    if source.is_dir():
        py = source / "submission.py"
    elif source.is_file():
        py = source
    else:
        raise CodabenchError(f"Submission path not found: {source}")

    if py.name != "submission.py":
        raise CodabenchError(
            f"Expected a file named submission.py, got {py.name}. "
            "Codabench requires submission.py at the zip root."
        )
    if not py.is_file():
        raise CodabenchError(f"submission.py not found at {py}")

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "submission.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(py, arcname="submission.py")  # at the root, no parent folder
    return zip_path


def download_and_extract(url: str, label: str, out_dir: Path, verbose: bool) -> dict:
    """Download a result zip, extract its json files, return {name: parsed_json}."""
    if not url:
        return {}
    if verbose:
        print(f"Downloading {label} result...", flush=True)
    resp = requests.get(url)
    resp.raise_for_status()
    dest = out_dir / f"{label}_result.zip"
    dest.write_bytes(resp.content)

    parsed = {}
    extract_dir = out_dir / f"{label}_result"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(extract_dir)
        for name in zf.namelist():
            if name.endswith(".json"):
                try:
                    parsed[Path(name).name] = json.loads(zf.read(name))
                except (ValueError, KeyError):
                    pass
    return parsed


def download_logs(details: dict, out_dir: Path, verbose: bool) -> list:
    """Download the per-step program/ingestion stdout+stderr logs.

    `details['logs']` is a list of {name, data_file(url)} (e.g. prediction_stdout,
    scoring_stderr, ...). Saves each to out_dir/logs/<name>.txt. Returns the names
    saved.
    """
    logs = details.get("logs") or []
    if not logs:
        return []
    log_dir = out_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"Downloading {len(logs)} step log(s)...")
    saved = []
    for entry in logs:
        name = entry.get("name")
        url = entry.get("data_file")
        if not name or not url:
            continue
        try:
            resp = requests.get(url)
            resp.raise_for_status()
            (log_dir / f"{name}.txt").write_bytes(resp.content)
            saved.append(name)
        except requests.RequestException as exc:
            if verbose:
                print(f"  warning: could not fetch log {name}: {exc}")
    return saved


def print_scores(scoring: dict, prediction: dict) -> None:
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    scores = scoring.get("scores.json") or {}
    if scores:
        print("\nLeaderboard scores (scoring_result/scores.json):")
        width = max(len(k) for k in scores)
        for key, value in scores.items():
            if isinstance(value, float):
                print(f"  {key:<{width}} : {value:.6g}")
            else:
                print(f"  {key:<{width}} : {value}")

    metrics = prediction.get("metrics.json") or {}
    cases = metrics.get("cases") if isinstance(metrics, dict) else None
    if cases:
        print("\nPer-case metrics (prediction_result/metrics.json):")
        header = f"  {'model':<8} {'dataset':<10} {'ep':>4} {'mean_par':>9} {'transmit':>10}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for c in cases:
            print(
                f"  {str(c.get('model','')):<8} {str(c.get('dataset','')):<10} "
                f"{c.get('ep',''):>4} {c.get('mean_par',float('nan')):>9.4f} "
                f"{c.get('transmit_amount',''):>10}"
            )
    if not scores and not cases:
        print("\nNo scores.json / metrics.json found in the result archives.")
        print("Raw payloads saved under the output directory.")


# ---------------------------------------------------------------------------
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Automate a Codabench submission upload and extract results.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "submission",
        nargs="?",
        default="submissions/finance_band/submission.py",
        help="Path to submission.py, a directory containing it, or a prebuilt .zip.",
    )
    p.add_argument("--competition", type=int, default=DEFAULT_COMPETITION,
                   help="Codabench competition id.")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--username", default=os.environ.get("CODABENCH_USERNAME"))
    p.add_argument("--password", default=os.environ.get("CODABENCH_PASSWORD"))
    p.add_argument("--token", default=os.environ.get("CODABENCH_TOKEN"),
                   help="DRF auth token (alternative to username/password).")
    p.add_argument("--phase-id", type=int)
    p.add_argument("--phase-index", type=int)
    p.add_argument("--phase-name")
    p.add_argument("--list-phases", action="store_true",
                   help="Print the competition's phases and exit.")
    p.add_argument("--submission-id", type=int,
                   help="Skip upload; poll + fetch results for this submission id.")
    p.add_argument("--out", default=None,
                   help="Output directory for the zip + downloaded results. "
                        "Default: results/<timestamp>/")
    p.add_argument("--poll-interval", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=1800.0,
                   help="Max seconds to wait for the submission to finish.")
    p.add_argument("--no-wait", action="store_true",
                   help="Create the submission and exit without polling.")
    p.add_argument("-q", "--quiet", action="store_true")
    return p.parse_args(argv)


def make_client(args) -> CodabenchClient:
    if args.token:
        return CodabenchClient(args.base_url, args.token, verbose=not args.quiet)
    if not (args.username and args.password):
        raise CodabenchError(
            "No credentials. Set CODABENCH_TOKEN, or CODABENCH_USERNAME + "
            "CODABENCH_PASSWORD (or pass --username/--password)."
        )
    return CodabenchClient.login(
        args.base_url, args.username, args.password, verbose=not args.quiet
    )


def main(argv=None) -> int:
    # Load .env before parse_args so the env-based argument defaults pick it up.
    load_dotenv(*default_env_files())
    args = parse_args(argv)
    verbose = not args.quiet

    try:
        client = make_client(args)

        if args.list_phases:
            comp = client.get_competition(args.competition)
            print(f"Competition {args.competition}: {comp.get('title', '')!r}")
            for ph in comp.get("phases", []):
                print(
                    f"  index={ph.get('index')} id={ph.get('id')} "
                    f"status={ph.get('status')!r} name={ph.get('name')!r} "
                    f"tasks={CodabenchClient.phase_task_names(ph)}"
                )
            return 0

        out_dir = Path(
            args.out or f"results/{datetime.now():%Y%m%d-%H%M%S}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        # -- obtain a finished submission, either by uploading or by id ----
        if args.submission_id:
            submission_id = args.submission_id
            if verbose:
                print(f"Using existing submission {submission_id}")
        else:
            zip_path = build_submission_zip(Path(args.submission), out_dir)
            if verbose:
                print(f"Submission bundle: {zip_path}")

            comp = client.get_competition(args.competition)
            phase = client.select_phase(
                comp,
                phase_id=args.phase_id,
                phase_index=args.phase_index,
                phase_name=args.phase_name,
            )
            if verbose:
                print(
                    f"Target phase: index={phase.get('index')} id={phase['id']} "
                    f"name={phase.get('name')!r} status={phase.get('status')!r}"
                )

            data_key = client.upload_submission_file(zip_path, args.competition)
            # Omit `tasks` so the server evaluates all of the phase's tasks. The
            # phase payload only exposes PhaseTaskInstance ids / task UUIDs, not
            # the integer Task PKs the create endpoint expects, so sending them
            # would be wrong; the default (run all) is what we want anyway.
            submission = client.create_submission(data_key, phase["id"])
            submission_id = submission["id"]
            print(f"Submission created: id={submission_id}")
            print(f"  {client.base}/competitions/{args.competition}/"
                  f"#/participate-tab")

            if args.no_wait:
                return 0

        # -- wait + collect results ----------------------------------------
        final = client.poll(
            submission_id, interval=args.poll_interval, timeout=args.timeout
        )
        status = final.get("status")
        print(f"\nSubmission {submission_id} finished with status: {status}")
        if status != "Finished":
            details = final.get("status_details") or ""
            if details:
                print(f"Status details: {details}")
            print("Submission did not finish successfully; no scores to extract.")
            return 1

        details = client.get_submission_details(submission_id)
        scoring = download_and_extract(
            details.get("scoring_result"), "scoring", out_dir, verbose
        )
        prediction = download_and_extract(
            details.get("prediction_result"), "prediction", out_dir, verbose
        )
        download_logs(details, out_dir, verbose)

        # Also dump the raw submission scores from the API for convenience.
        (out_dir / "submission_scores.json").write_text(
            json.dumps(final.get("scores", []), indent=2)
        )

        print_scores(scoring, prediction)
        print(f"\nArtifacts saved to: {out_dir}/")
        return 0

    except CodabenchError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
