"""Parse every workflow and regression-check the colon-space failure class."""

from pathlib import Path

import yaml


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not paths:
        raise SystemExit("no_workflows")
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise SystemExit("invalid_workflow_shape")
    try:
        yaml.safe_load("run: printf 'Candidate only: invalid'\n")
    except yaml.YAMLError:
        pass
    else:
        raise SystemExit("parser_regression")
    print(f"Workflow YAML parse: {len(paths)} pass; invalid scalar rejected")


if __name__ == "__main__":
    main()
