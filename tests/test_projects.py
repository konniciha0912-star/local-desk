from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from projects import citations, overview, search
import project_tests


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.project = self.root / "project"; self.project.mkdir()
        (self.project / "average.py").write_text("def average(values):\n    # 数値の平均を計算する\n    return sum(values) / len(values)\n",encoding="utf-8")
        (self.project / "README.md").write_text("# 平均の計算ツール\nPythonの数値を処理する。",encoding="utf-8")
        (self.project / ".env.txt").write_text("secret average token")
        self.application = app.Application(self.root / "data")

    def tearDown(self): self.temp.cleanup()

    def test_japanese_and_identifier_search_stays_in_allowed_project(self):
        for query in ["平均を計算", "average values"]:
            result = search(self.application,str(self.project),query)
            self.assertTrue(result["results"])
            self.assertTrue(any(item["path"] == "average.py" for item in result["results"]))
            self.assertTrue(all(item["path"] != ".env.txt" for item in result["results"]))
            self.assertTrue(all(item["start"] >= 1 and item["end"] <= 3 for item in result["results"] if item["path"] == "average.py"))

    def test_overview_identifies_definitions_and_readme_without_execution(self):
        result = overview(self.application,str(self.project))
        self.assertEqual(result["suggested"],["README.md"])
        self.assertEqual(result["python"][0]["definitions"][0]["name"],"average")
        self.assertEqual(result["python"][0]["definitions"][0]["line"],1)

    def test_citation_range_validation_flags_invented_sources(self):
        sources = [{"path":"average.py","start":1,"included_end":3}]
        result = citations("[average.py:L1-L3] [average.py:L9] [other.py:L1]",sources)
        self.assertEqual([item["verified"] for item in result],[True,False,False])

    def test_test_runner_requires_confirmation_and_only_predefined_commands(self):
        for kind, confirmed in [("shell",True),("unittest",False)]:
            with self.assertRaises(app.AppError): project_tests.run(str(self.project),kind,confirmed)
        tests = self.project / "tests"; tests.mkdir()
        (tests / "test_sample.py").write_text("import unittest\nclass Sample(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(2 + 2, 4)\n")
        result = project_tests.run(str(self.project),"unittest",True)
        self.assertEqual(result["exit_code"],0,result["output"])
        self.assertIn("OK",result["output"])
        self.assertFalse(result["timed_out"])


if __name__ == "__main__": unittest.main()
