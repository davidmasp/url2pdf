"""Publisher configuration, retrieval, fallback, and filesystem regressions."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import Error, TimeoutError
from typer.testing import CliRunner

from urls2pdf.cli import app
from urls2pdf.papers import (
    PaperRule, PapersConfigError, PaperUnavailable, discover_pdf, download_pdf,
    load_rules, save_pdf,
)
from urls2pdf.renderer import PDFRenderer, RenderResult
from urls2pdf.urls import pdf_filename


PDF = b"%PDF-1.7\nfixture bytes\n%%EOF\n"
ARTICLE = "https://www.nature.com/articles/paper"


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "journals.toml"

    def test_overrides_replace_whole_rule_and_add_case_normalized_host(self):
        self.path.write_text('''
[papers.domains."WWW.NATURE.COM"]
selectors = ['a.custom', 'a.alternative']
attribute = "data-pdf"
[papers.domains."journal.example"]
selectors = ['a.pdf']
''')
        rules = load_rules(self.path)
        self.assertEqual(rules["www.nature.com"], PaperRule(("a.custom", "a.alternative"), "data-pdf"))
        self.assertEqual(rules["journal.example"].attribute, "href")
        self.assertIn("data-test", load_rules()["www.nature.com"].selectors[0])

    def test_malformed_configs_are_clear_errors(self):
        bad_configs = [
            "not toml", "", "papers = []", "[papers]\ndomains = []",
            '[wrong]\nx = 1', '[papers.domains."https://nature.com"]\nselectors = ["a"]',
            '[papers.domains."*.nature.com"]\nselectors = ["a"]',
            '[papers.domains."nature.com:443"]\nselectors = ["a"]',
            '[papers.domains." nature.com"]\nselectors = ["a"]',
            '[papers.domains."nature.com"]\nselectors = []',
            '[papers.domains."nature.com"]\nselectors = [" "]',
            '[papers.domains."nature.com"]\nselectors = "a"',
            '[papers.domains."nature.com"]\nselectors = [1]',
            '[papers.domains."nature.com"]\nselectors = ["a"]\nattribute = ""',
            '[papers.domains."nature.com"]\nselectors = ["a"]\nattribute = 1',
            '[papers.domains."nature.com"]\nselectors = ["a"]\nunknown = true',
            '[papers.domains."nature.com"]\nselectors = ["a"]\n[papers.domains."NATURE.COM"]\nselectors = ["b"]',
        ]
        for config in bad_configs:
            with self.subTest(config=config):
                self.path.write_text(config)
                with self.assertRaises(PapersConfigError):
                    load_rules(self.path)
        self.path.unlink()
        with self.assertRaisesRegex(PapersConfigError, "Cannot read papers config"):
            load_rules(self.path)
        with self.assertRaises(PapersConfigError):
            load_rules(Path(self.directory.name))


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.rule = PaperRule(("a.pdf", "a.alternative"))

    def test_document_base_query_and_reference_suffix_preserved_and_deduplicated(self):
        self.page.evaluate.return_value = {
            "base": "https://www.nature.com/articles/assets/",
            "links": ["../paper_reference.pdf?download=1", "../paper_reference.pdf?download=1"],
        }
        self.assertEqual(discover_pdf(self.page, "www.nature.com", self.rule),
                         "https://www.nature.com/articles/paper_reference.pdf?download=1")
        self.page.evaluate.assert_called_once()

    def test_selector_alternatives_skip_empty_and_non_http_links(self):
        self.page.evaluate.side_effect = [
            {"base": ARTICLE, "links": [None, "", " ", "javascript:alert(1)", "mailto:a@b", "https://bad:port/file", "https://[broken"]},
            {"base": ARTICLE, "links": ["//cdn.example/paper.pdf?key=abc"]},
        ]
        self.assertEqual(discover_pdf(self.page, "www.nature.com", self.rule), "https://cdn.example/paper.pdf?key=abc")
        self.assertEqual(self.page.evaluate.call_count, 2)

    def test_ambiguity_does_not_pick_an_arbitrary_link_or_later_selector(self):
        self.page.evaluate.return_value = {"base": ARTICLE, "links": ["a.pdf", "b.pdf"]}
        with self.assertRaisesRegex(PaperUnavailable, "Ambiguous"):
            discover_pdf(self.page, "www.nature.com", self.rule)
        self.page.evaluate.assert_called_once()

    def test_invalid_css_is_a_configuration_error_with_host_and_selector(self):
        self.page.evaluate.return_value = {"error": "Invalid selector"}
        with self.assertRaisesRegex(PapersConfigError, r"www.nature.com.*a.pdf"):
            discover_pdf(self.page, "www.nature.com", self.rule)

    def test_absent_buttons_report_fallback_reason(self):
        self.page.evaluate.return_value = {"base": ARTICLE, "links": []}
        with self.assertRaisesRegex(PaperUnavailable, "No usable"):
            discover_pdf(self.page, "www.nature.com", self.rule)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.response = self.context.request.get.return_value
        self.response.ok = True
        self.response.status = 200
        self.response.headers = {"content-type": "application/pdf"}
        self.response.body.return_value = PDF

    def download(self):
        return download_pdf(self.context, ARTICLE + "_reference.pdf", ARTICLE, 30_000)

    def test_valid_pdf_and_octet_stream_are_returned_unchanged_with_bounded_request(self):
        for content_type in ("application/pdf; charset=binary", "APPLICATION/OCTET-STREAM"):
            with self.subTest(content_type=content_type):
                self.response.headers = {"content-type": content_type}
                self.assertEqual(self.download(), PDF)
        self.context.request.get.assert_called_with(
            ARTICLE + "_reference.pdf", headers={"Referer": ARTICLE}, timeout=30_000, max_redirects=5,
        )
        self.assertEqual(self.response.dispose.call_count, 2)

    def test_invalid_responses_are_disposed(self):
        cases = [
            (False, 403, "application/pdf", PDF, "HTTP 403"),
            (False, 500, "application/pdf", PDF, "HTTP 500"),
            (True, 200, "text/html", b"<html>login</html>", "content type"),
            (True, 200, "image/png", PDF, "content type"),
            (True, 200, "", PDF, "content type"),
            (True, 200, "application/pdf", b"", "PDF header"),
            (True, 200, "application/pdf", b"%PDF", "PDF header"),
            (True, 200, "application/pdf", b"%PDF-", "PDF header"),
            (True, 200, "application/pdf", b"<html>challenge</html>", "PDF header"),
        ]
        for ok, status, content_type, body, message in cases:
            with self.subTest(status=status, content_type=content_type, body=body):
                self.response.reset_mock()
                self.response.ok, self.response.status = ok, status
                self.response.headers = {"content-type": content_type}
                self.response.body.return_value = body
                with self.assertRaisesRegex(PaperUnavailable, message):
                    self.download()
                self.response.dispose.assert_called_once()

    def test_timeout_and_body_errors_are_fallbacks(self):
        self.context.request.get.side_effect = TimeoutError("timed out")
        with self.assertRaisesRegex(PaperUnavailable, "timed out"):
            self.download()
        self.response.dispose.assert_not_called()
        self.context.request.get.side_effect = None
        self.response.body.side_effect = Error("body interrupted")
        with self.assertRaisesRegex(PaperUnavailable, "body interrupted"):
            self.download()
        self.response.dispose.assert_called_once()

    def test_dispose_error_preserves_download_error(self):
        self.response.ok = False
        self.response.status = 403
        self.response.dispose.side_effect = Error("already closed")
        with self.assertRaisesRegex(PaperUnavailable, "HTTP 403"):
            self.download()

    def test_dispose_error_after_valid_body_reports_a_fallback(self):
        self.response.dispose.side_effect = Error("request context closed")
        with self.assertRaisesRegex(PaperUnavailable, "cleanup failed"):
            self.download()


class PapersRendererTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        patcher = patch("urls2pdf.renderer.sync_playwright")
        playwright = patcher.start().return_value.__enter__.return_value
        self.addCleanup(patcher.stop)
        self.browser = playwright.chromium.launch.return_value
        self.context = self.browser.new_context.return_value
        self.page = self.make_page(ARTICLE)
        self.context.new_page.return_value = self.page
        self.context.request.get.return_value.ok = True
        self.context.request.get.return_value.headers = {"content-type": "application/pdf"}
        self.context.request.get.return_value.body.return_value = PDF

    def make_page(self, url):
        page = MagicMock()
        page.url = url
        page.goto.return_value.status = 200
        page.evaluate.return_value = {"base": url, "links": ["paper_reference.pdf", "paper_reference.pdf", "paper_reference.pdf"]}
        return page

    def render(self, urls=None, **kwargs):
        return PDFRenderer(self.output, papers=True, **kwargs).render(urls or [ARTICLE])

    def test_download_uses_button_once_saves_original_bytes_and_closes_resources(self):
        result = self.render()[0]
        self.assertEqual(result.method, "downloaded")
        self.assertEqual(result.pdf_url, ARTICLE + "_reference.pdf")
        self.assertEqual(result.pdf_path, self.output / pdf_filename(ARTICLE))
        self.assertEqual(result.pdf_path.read_bytes(), PDF)
        self.context.request.get.assert_called_once()
        self.page.pdf.assert_not_called()
        self.page.close.assert_called_once()
        self.context.request.get.return_value.dispose.assert_called_once()
        self.context.close.assert_called_once()
        self.browser.close.assert_called_once()

    def test_default_mode_never_discovers_or_requests_and_keeps_print_settings(self):
        result = PDFRenderer(self.output).render([ARTICLE])[0]
        self.assertEqual(result.method, "rendered")
        self.page.evaluate.assert_not_called()
        self.context.request.get.assert_not_called()
        self.page.pdf.assert_called_once_with(
            path=str(self.output / pdf_filename(ARTICLE)), format="A4", print_background=True,
            margin={side: "0.5in" for side in ("top", "bottom", "left", "right")},
        )

    def test_exact_host_matching_and_unrelated_redirect(self):
        for host in ("evilwww.nature.com", "www.nature.com.evil.example", "sub.www.nature.com", "example.org"):
            with self.subTest(host=host):
                self.page.reset_mock()
                self.page.url = f"https://{host}/paper"
                result = self.render()[0]
                self.assertIn("Redirected", result.fallback_reason)
                self.page.evaluate.assert_not_called()
                self.context.request.get.assert_not_called()
                self.page.pdf.assert_called_once()

    def test_host_is_case_insensitive_and_custom_rules_use_final_host(self):
        self.page.url = "https://JOURNAL.EXAMPLE/article"
        self.page.evaluate.return_value = {"base": self.page.url, "links": ["file.pdf"]}
        result = self.render(papers_rules={"journal.example": PaperRule(("a.custom",), "data-pdf")})[0]
        self.assertEqual(result.method, "downloaded")
        self.assertEqual(self.page.evaluate.call_args.args[1], {"selector": "a.custom", "attribute": "data-pdf"})

    def test_supported_host_failures_render_and_report_diagnostics(self):
        for problem in ("missing", "ambiguous", "download"):
            with self.subTest(problem=problem):
                self.page.reset_mock()
                self.page.evaluate.return_value = {"base": ARTICLE, "links": [] if problem == "missing" else ["a.pdf", "b.pdf"]}
                if problem == "download":
                    self.page.evaluate.return_value["links"] = ["a.pdf"]
                    self.context.request.get.side_effect = TimeoutError("PDF timeout")
                result = self.render()[0]
                self.assertIsNone(result.error)
                self.assertEqual(result.method, "rendered")
                self.assertTrue(result.fallback_reason)
                self.page.pdf.assert_called_once()
                self.page.close.assert_called_once()

    def test_both_paths_failing_retains_errors_and_continues(self):
        self.context.request.get.side_effect = TimeoutError("PDF timeout")
        self.page.pdf.side_effect = Error("printing failed")
        good = self.make_page("https://example.org/good")
        self.context.new_page.side_effect = [self.page, good]
        results = self.render([ARTICLE, "https://example.org/good"])
        self.assertIn("PDF timeout", results[0].fallback_reason)
        self.assertEqual(results[0].error, "printing failed")
        self.assertIsNone(results[1].error)
        good.pdf.assert_called_once()
        self.page.close.assert_called_once()
        good.close.assert_called_once()

    def test_http_error_pages_fail_without_download_or_render_and_continue(self):
        for status in (404, 500):
            with self.subTest(status=status):
                self.page.reset_mock()
                self.page.goto.return_value.status = status
                good = self.make_page("https://example.org/good")
                self.context.new_page.side_effect = [self.page, good]
                results = self.render([ARTICLE, "https://example.org/good"])
                self.assertIn(f"HTTP {status}", results[0].error)
                self.page.pdf.assert_not_called()
                self.page.evaluate.assert_not_called()
                self.assertIsNone(results[1].error)

    def test_no_navigation_response_still_renders(self):
        self.page.goto.return_value = None
        self.page.url = "https://example.org"
        self.assertIsNone(self.render()[0].error)
        self.page.pdf.assert_called_once()

    def test_invalid_selector_closes_all_resources_and_does_not_fallback(self):
        self.page.evaluate.return_value = {"error": "invalid CSS"}
        with self.assertRaises(PapersConfigError):
            self.render()
        self.page.pdf.assert_not_called()
        self.page.close.assert_called_once()
        self.context.close.assert_called_once()
        self.browser.close.assert_called_once()

    def test_cleanup_errors_preserve_saved_pdf_or_original_failure_and_continue(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.page.goto.side_effect = Error("navigation failed") if fail else None
                self.page.close.side_effect = Error("cleanup failed")
                good = self.make_page("https://example.org/good")
                self.context.new_page.side_effect = [self.page, good]
                results = self.render([ARTICLE, "https://example.org/good"])
                self.assertEqual(results[0].error, "navigation failed" if fail else None)
                self.assertEqual(results[0].cleanup_error, "cleanup failed")
                self.assertIsNone(results[1].error)

    @patch("urls2pdf.papers.Path.replace", side_effect=OSError("disk full"))
    def test_download_write_failure_preserves_destination_and_removes_temporary(self, replace):
        destination = self.output / pdf_filename(ARTICLE)
        destination.write_bytes(b"old PDF")
        result = self.render()[0]
        self.assertEqual(result.error, "disk full")
        self.assertEqual(destination.read_bytes(), b"old PDF")
        self.assertEqual(list(self.output.glob(".urls2pdf-*")), [])
        self.page.pdf.assert_not_called()
        self.page.close.assert_called_once()

    def test_download_write_failure_continues_to_next_download(self):
        second_url = ARTICLE + "-next"
        second_page = self.make_page(second_url)
        self.context.new_page.side_effect = [self.page, second_page]
        with patch("urls2pdf.renderer.save_pdf", side_effect=[OSError("disk full"), None]) as save:
            results = self.render([ARTICLE, second_url])
        self.assertEqual(results[0].error, "disk full")
        self.assertIsNone(results[1].error)
        self.assertEqual(results[1].method, "downloaded")
        self.assertEqual(save.call_count, 2)
        second_page.close.assert_called_once()

    def test_empty_renderer_does_not_start_browser(self):
        self.assertEqual(PDFRenderer(self.output, papers=True).render(iter([])), [])
        self.browser.new_context.assert_not_called()


class AtomicWriteTests(unittest.TestCase):
    def test_partial_write_preserves_existing_file_and_removes_temporary(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "paper.pdf"
            destination.write_bytes(b"old PDF")
            from urls2pdf import papers
            original = papers.NamedTemporaryFile

            def failing_file(**kwargs):
                target = original(**kwargs)
                write = target.write

                def partial_write(body):
                    write(body[:8])
                    raise OSError("write interrupted")

                target.write = partial_write
                return target

            with patch("urls2pdf.papers.NamedTemporaryFile", side_effect=failing_file):
                with self.assertRaisesRegex(OSError, "write interrupted"):
                    save_pdf(destination, PDF)
            self.assertEqual(destination.read_bytes(), b"old PDF")
            self.assertEqual(list(Path(directory).iterdir()), [destination])


class PapersCLITests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "urls.txt"
        self.source.write_text(ARTICLE + "\n")
        self.runner = CliRunner()

    @patch("urls2pdf.cli.PDFRenderer")
    def test_config_requires_papers_and_invalid_file_fails_before_browser(self, renderer):
        config = str(self.source.with_name("missing.toml"))
        for flags in (["--papers-config", config], ["--papers", "--papers-config", config]):
            with self.subTest(flags=flags):
                result = self.runner.invoke(app, ["render", str(self.source), *flags])
                self.assertEqual(result.exit_code, 2, result.output)
                renderer.assert_not_called()

    @patch("urls2pdf.cli.PDFRenderer")
    def test_empty_papers_input_needs_no_browser_or_config_loading(self, renderer):
        self.source.write_text("# empty\n")
        result = self.runner.invoke(app, ["render", str(self.source), "--papers", "--papers-config", "missing.toml"])
        self.assertEqual(result.exit_code, 0, result.output)
        renderer.assert_not_called()

    @patch("urls2pdf.cli.PDFRenderer")
    def test_counts_fallback_and_cleanup_diagnostics_with_partial_failure(self, renderer):
        renderer.return_value.render.return_value = [
            RenderResult(ARTICLE, Path("paper.pdf"), method="downloaded"),
            RenderResult("https://example.org", Path("page.pdf"), fallback_reason="No usable PDF", cleanup_error="close failed"),
            RenderResult("https://example.org/bad", error="HTTP 404"),
        ]
        result = self.runner.invoke(app, ["render", str(self.source), "--papers"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("1 downloaded; 1 rendered; 1 failed", result.output)
        self.assertIn("Rendering fallback", result.output)
        self.assertIn("No usable PDF", result.output)
        self.assertIn("Page cleanup warning", result.output)
        self.assertIn("HTTP 404", result.output)
        self.assertTrue(renderer.call_args.kwargs["papers"])

    @patch("urls2pdf.cli.PDFRenderer")
    def test_successful_fallback_is_success_and_runtime_selector_error_is_usage_error(self, renderer):
        renderer.return_value.render.return_value = [RenderResult(ARTICLE, Path("page.pdf"), fallback_reason="PDF access denied")]
        result = self.runner.invoke(app, ["render", str(self.source), "--papers"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("0 downloaded; 1 rendered; 0 failed", result.output)
        renderer.return_value.render.side_effect = PapersConfigError("Invalid CSS selector for www.nature.com: '['")
        result = self.runner.invoke(app, ["render", str(self.source), "--papers"])
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("www.nature.com", result.output)


if __name__ == "__main__":
    unittest.main()
