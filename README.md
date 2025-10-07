## urls2pdf

Render a list of web pages to PDFs using Playwright.

### Requirements

- Python 3.11+
- [Playwright](https://playwright.dev/python/)
- [Click](https://click.palletsprojects.com/)

Install the dependencies recorded in `pyproject.toml` and download the browsers Playwright needs:

```fish
uv sync
uv run playwright install
```

Need to add another dependency later? Use `uv add <package>` and it will be written back to `pyproject.toml` automatically.

### Run

Convert a list of URLs (one per line) into PDFs:

```fish
# create (or modify) the url file
$ cat urls.txt 
https://en.wikipedia.org/wiki/Celltech
https://en.wikipedia.org/wiki/Mary_E._Brunkow
https://en.wikipedia.org/wiki/Nobel_Prize_in_Physiology_or_Medicine

# run the script
uv run script.py urls.txt

# this will create a series of pdfs in the pdfs directory
eza --tree pdfs/
pdfs
├── en.wikipedia.org_wiki_Celltech.pdf
├── en.wikipedia.org_wiki_Mary_E._Brunkow.pdf
└── en.wikipedia.org_wiki_Nobel_Prize_in_Physiology_or_Medicine.pdf
```
Use `uv run script.py --help` for available options.
