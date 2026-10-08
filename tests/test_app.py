"""Regression tests for the packaged CLI and browser lifecycle, without downloads."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import Error, TimeoutError
from typer.testing import CliRunner

from urls2pdf.browser import BrowserInstaller
from urls2pdf.cli import app
from urls2pdf.renderer import PDFRenderer, RenderResult
from urls2pdf.urls import URLFile, pdf_filename


class InputTests(unittest.TestCase):
    def test_comments_whitespace_and_url_fragments(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "urls.txt"
            path.write_text(" # comment\n\n https://example.org/page#section \n", encoding="utf-8")
            self.assertEqual(URLFile(path).read(), ["https://example.org/page#section"])

    def test_legacy_pdf_filenames(self):
        self.assertEqual(
            pdf_filename("https://en.wikipedia.org/wiki/Celltech"),
            "en.wikipedia.org_wiki_Celltech.pdf",
        )
        self.assertEqual(pdf_filename("https://example.org/page?a=1&b=2"),
                         "example.org_page_a-1_b-2.pdf")
        self.assertLessEqual(len(pdf_filename("https://example.org/" + "x" * 500)), 204)


class BrowserTests(unittest.TestCase):
    @patch("urls2pdf.browser.subprocess.run")
    def test_installer_uses_tool_python_and_chromium(self, run):
        BrowserInstaller().install()
        run.assert_called_once_with(
            [sys.executable, "-m", "playwright", "install", "chromium"], check=True
        )

    @patch("urls2pdf.browser.subprocess.run")
    def test_system_dependencies_are_opt_in(self, run):
        BrowserInstaller().install(with_deps=True)
        self.assertIn("--with-deps", run.call_args.args[0])


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "pdfs"
        patcher = patch("urls2pdf.renderer.sync_playwright")
        self.playwright = patcher.start().return_value.__enter__.return_value
        self.addCleanup(patcher.stop)
        self.browser = self.playwright.chromium.launch.return_value
        self.context = self.browser.new_context.return_value
        self.context.new_page.return_value.goto.return_value.status = 200

    def test_page_failure_does_not_stop_next_page_and_resources_close(self):
        bad_page, good_page = MagicMock(), MagicMock()
        good_page.goto.return_value.status = 200
        bad_page.goto.side_effect = TimeoutError("network busy")
        self.context.new_page.side_effect = [bad_page, good_page]
        results = PDFRenderer(self.output).render(
            ["https://example.org/bad", "https://example.org/good"]
        )
        self.assertIn("Timed out", results[0].error)
        self.assertIsNone(results[0].pdf_path)
        self.assertEqual(results[1].pdf_path, self.output / "example.org_good.pdf")
        self.assertIsNone(results[1].error)
        bad_page.close.assert_called_once()
        good_page.close.assert_called_once()
        self.context.close.assert_called_once()
        self.browser.close.assert_called_once()

    def test_pdf_write_failure_is_reported_and_page_closes(self):
        page = self.context.new_page.return_value
        page.pdf.side_effect = OSError("disk full")
        result = PDFRenderer(self.output).render(["https://example.org"])[0]
        self.assertEqual(result.error, "disk full")
        page.close.assert_called_once()
        self.context.close.assert_called_once()
        self.browser.close.assert_called_once()

    def test_context_creation_failure_closes_browser(self):
        self.browser.new_context.side_effect = Error("context failed")
        with self.assertRaises(Error):
            PDFRenderer(self.output).render(["https://example.org"])
        self.browser.close.assert_called_once()

    def test_unexpected_exception_still_closes_resources(self):
        page = self.context.new_page.return_value
        page.pdf.side_effect = RuntimeError("unexpected bug")
        with self.assertRaises(RuntimeError):
            PDFRenderer(self.output).render(["https://example.org"])
        page.close.assert_called_once()
        self.context.close.assert_called_once()
        self.browser.close.assert_called_once()


class CLITests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "urls.txt"
        self.source.write_text("https://example.org\n", encoding="utf-8")

    def test_help_and_version(self):
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("install-browser", result.output)
        self.assertIn("render", result.output)
        result = self.runner.invoke(app, ["--version"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("urls2pdf 0.2.1", result.output)

    @patch("urls2pdf.cli.PDFRenderer")
    def test_empty_input_needs_no_browser(self, renderer):
        self.source.write_text("# comment\n\n", encoding="utf-8")
        result = self.runner.invoke(app, ["render", str(self.source)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No URLs", result.output)
        renderer.assert_not_called()

    @patch("urls2pdf.cli.PDFRenderer")
    def test_success_and_custom_output_dir(self, renderer):
        renderer.return_value.render.return_value = [RenderResult("https://example.org", Path("page.pdf"))]
        output = Path(self.directory.name) / "custom"
        result = self.runner.invoke(app, ["render", str(self.source), "-o", str(output)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Saved 1 PDF(s); 0 failed", result.output)
        renderer.assert_called_once_with(output)

    @patch("urls2pdf.cli.PDFRenderer")
    def test_partial_failure_has_nonzero_exit_and_summary(self, renderer):
        renderer.return_value.render.return_value = [
            RenderResult("https://example.org", Path("page.pdf")),
            RenderResult("https://example.org/bad", error="navigation failed"),
        ]
        result = self.runner.invoke(app, ["render", str(self.source)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Saved 1 PDF(s); 1 failed", result.output)
        self.assertIn("navigation failed", result.output)

    @patch("urls2pdf.cli.PDFRenderer")
    def test_missing_browser_reports_install_command(self, renderer):
        renderer.return_value.render.side_effect = Error("Executable doesn't exist")
        result = self.runner.invoke(app, ["render", str(self.source)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("urls2pdf install-browser", result.output)

    def test_missing_input_is_a_usage_error(self):
        result = self.runner.invoke(app, ["render", str(self.source.with_name("missing.txt"))])
        self.assertEqual(result.exit_code, 2)

    def test_invalid_utf8_reports_input_error_without_browser_advice(self):
        self.source.write_bytes(b"\xff")
        result = self.runner.invoke(app, ["render", str(self.source)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Rendering failed", result.output)
        self.assertNotIn("install-browser", result.output)

    @patch("urls2pdf.cli.BrowserInstaller")
    def test_install_command_forwards_options(self, installer):
        result = self.runner.invoke(app, ["install-browser", "--with-deps"])
        self.assertEqual(result.exit_code, 0, result.output)
        installer.return_value.install.assert_called_once_with(with_deps=True)

    @patch("urls2pdf.cli.BrowserInstaller")
    def test_installer_failure_has_nonzero_exit(self, installer):
        installer.return_value.install.side_effect = subprocess.CalledProcessError(1, ["playwright"])
        result = self.runner.invoke(app, ["install-browser"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Browser installation failed", result.output)


if __name__ == "__main__":
    unittest.main()
