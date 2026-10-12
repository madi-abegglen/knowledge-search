"""Negative test: the audit must still FAIL when a lockfile contains a known-vulnerable pin.

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_audit_bites.py

Builds scratch copies of the repo's audit inputs (check_env.py, audit_exceptions.txt, both lockfiles) in a
temporary folder, appends a known-vulnerable pure-Python pin to ONE lockfile at a time, runs the copied
check_env.py --audit there, and asserts a non-zero exit with a FAIL naming that file. The real
requirements files, check_env.py and audit_exceptions.txt are never modified (hashes are re-checked).
Needs network access (pip-audit resolves and queries the vulnerability service).
"""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
VULNERABLE_PIN = "PyJWT==1.5.0"          # pure-Python wheel; multiple published advisories


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def scratch_copy(extra_line_in: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ks_audit_bite_"))
    (tmp / "scripts").mkdir()
    for name in ("check_env.py", "audit_exceptions.txt"):
        shutil.copyfile(ROOT / "scripts" / name, tmp / "scripts" / name)
    for name in ("requirements.txt", "requirements-dev.txt"):
        shutil.copyfile(ROOT / name, tmp / name)
    target = tmp / extra_line_in
    with target.open("ab") as handle:
        handle.write(f"{VULNERABLE_PIN}\r\n".encode())
    return tmp


def run_audit(folder: Path) -> tuple[int, str]:
    res = subprocess.run([sys.executable, str(folder / "scripts" / "check_env.py"), "--audit"],
                         cwd=folder, capture_output=True, text=True)
    return res.returncode, res.stdout + res.stderr


def main() -> int:
    guarded = [ROOT / "requirements.txt", ROOT / "requirements-dev.txt",
               ROOT / "scripts" / "check_env.py", ROOT / "scripts" / "audit_exceptions.txt"]
    before = {p.name: digest(p) for p in guarded}
    ok = True
    for lockfile in ("requirements.txt", "requirements-dev.txt"):
        folder = scratch_copy(lockfile)
        code, output = run_audit(folder)
        fail_lines = [l for l in output.splitlines() if "[FAIL]" in l and "pip-audit" in l]
        names_file = any(lockfile in l for l in fail_lines)
        found = "PyJWT" in output or "PYSEC" in output or "GHSA" in output or "CVE-" in output
        print(f"vulnerable pin appended to {lockfile}: exit={code}, FAIL line names the file={names_file}, "
              f"advisory text shown={found}")
        for line in fail_lines:
            print("   ", line.strip()[:200])
        ok &= code != 0 and names_file
    after = {p.name: digest(p) for p in guarded}
    print("real files unchanged by the test:", before == after)
    ok &= before == after
    print("RESULT:", "PASS (the audit still bites)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
