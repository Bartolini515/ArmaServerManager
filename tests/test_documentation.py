from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")

REQUIRED_DOCUMENTS = {
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "backend" / "AGENTS.md",
    ROOT / "frontend" / "AGENTS.md",
    ROOT / "docs" / "architecture.md",
    ROOT / "docs" / "code-map.md",
    ROOT / "docs" / "api.md",
    ROOT / "docs" / "data-model.md",
    ROOT / "docs" / "configuration.md",
    ROOT / "docs" / "domain" / "instance-lifecycle.md",
    ROOT / "docs" / "domain" / "files-and-missions.md",
    ROOT / "docs" / "known-risks.md",
    ROOT / "docs" / "troubleshooting.md",
    ROOT / "docs" / "decisions" / "README.md",
}


class DocumentationContractTests(unittest.TestCase):
    def test_required_documentation_exists(self) -> None:
        missing = sorted(
            str(path.relative_to(ROOT))
            for path in REQUIRED_DOCUMENTS
            if not path.is_file()
        )
        self.assertEqual([], missing, f"Missing documentation: {missing}")

    def test_relative_markdown_links_resolve(self) -> None:
        missing: list[str] = []
        for document in REQUIRED_DOCUMENTS:
            if not document.is_file():
                continue
            for raw_target in MARKDOWN_LINK.findall(
                document.read_text(encoding="utf-8")
            ):
                target = raw_target.strip().split()[0].strip("<>")
                if target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                relative_path = unquote(target.split("#", 1)[0])
                if not relative_path:
                    continue
                resolved = (document.parent / relative_path).resolve()
                if not resolved.exists():
                    missing.append(f"{document.relative_to(ROOT)} -> {target}")

        self.assertEqual([], missing, f"Broken relative Markdown links: {missing}")


if __name__ == "__main__":
    unittest.main()
