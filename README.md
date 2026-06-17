# ins-tunisia-mcp

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20738616.svg)](https://doi.org/10.5281/zenodo.20738616)

An AI-powered [MCP](https://modelcontextprotocol.io) server that turns Tunisia's national statistics portal ([dataportal.ins.tn](http://dataportal.ins.tn)) into a conversational data source. [MCP](https://modelcontextprotocol.io) (Model Context Protocol) lets AI assistants call external tools — connect your AI client and start asking questions in natural language.

The portal is the **Institut National de la Statistique (INS)** data portal, built on the Prognoz platform: dimensional time-series cubes covering national accounts, the 2014 census, population projections, employment, household conditions, foreign trade, and more. The API speaks XML; ins-tunisia-mcp owns all of it — tools take structured parameters and return clean JSON, so the AI never sees an angle bracket.

Built for researchers, data scientists, economists, and journalists working with Tunisian official statistics.

> ins-tunisia-mcp is distributed as the `ins-mcp` Python package (the module is run as `python -m ins_mcp`); the project is published by the tanitdata studio.

https://github.com/user-attachments/assets/3905f814-b6ae-423a-a648-f603ee422c8a

*Demo: asking ins-tunisia-mcp for Tunisian statistics in natural language and getting structured, sourced answers. ([direct link](Video_Demo/demo_short.mp4))*


---

## Getting Started

ins-tunisia-mcp runs locally as a stdio MCP server. Clone the repo, install dependencies, and point your MCP client at the local process.

### Prerequisites

- Python 3.10+

### Install and run

```bash
git clone https://github.com/tanitdata/ins-tunisia-mcp.git
cd ins-tunisia-mcp
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # POSIX
# .venv\Scripts\python -m pip install -e ".[dev]"   # Windows
.venv/bin/python -m ins_mcp                      # starts stdio server
```

On first run the server seeds an on-disk catalogue cache from a single `GetStructure` call (~2–4s). Per-source `has_data` flags then fill in shortly after via a bounded-concurrency background probe — the first tool call is never blocked on probing. Use the `ins_refresh_catalogue` tool to re-pull the catalogue on demand.

### Connect your MCP client

ins-tunisia-mcp is a standard **stdio** MCP server, so it works with any MCP-compatible client. They all need the same two things — the Python interpreter from your virtualenv and the `-m ins_mcp` arguments. Use the **absolute path** to the venv's Python (`.venv/bin/python` on macOS/Linux, `.venv\Scripts\python.exe` on Windows), and after updating the code, restart the client so it respawns the server.

Most clients share this JSON shape (see [`claude_desktop_config.example.json`](claude_desktop_config.example.json)):

```json
{
  "mcpServers": {
    "ins": {
      "command": "/absolute/path/to/ins-tunisia-mcp/.venv/bin/python",
      "args": ["-m", "ins_mcp"]
    }
  }
}
```

<details>
<summary><b>Claude Desktop</b></summary>

Edit the config file (create it if absent) and add the block above under `mcpServers`:
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Fully quit and reopen Claude Desktop. The `ins` tools appear in the tools menu.
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add ins -- /absolute/path/to/ins-tunisia-mcp/.venv/bin/python -m ins_mcp
```

Or add the JSON block to `.mcp.json` in your project root.
</details>

<details>
<summary><b>Cursor</b></summary>

Create `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` for global) with the JSON block above. Reload Cursor; enable the server under **Settings → MCP**.
</details>

<details>
<summary><b>VS Code (Copilot / Agent mode)</b></summary>

Add to `.vscode/mcp.json` (workspace) or your user `settings.json` under `"mcp"`. VS Code uses a `servers` key:

```json
{
  "servers": {
    "ins": {
      "type": "stdio",
      "command": "/absolute/path/to/ins-tunisia-mcp/.venv/bin/python",
      "args": ["-m", "ins_mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>Cline / Roo Code (VS Code extensions)</b></summary>

Open the extension's **MCP Servers** panel → **Edit / Configure MCP Servers**, and add the JSON block above to `cline_mcp_settings.json`.
</details>

<details>
<summary><b>Windsurf</b></summary>

Add the JSON block to `~/.codeium/windsurf/mcp_config.json`, then reload from **Settings → Cascade → MCP servers**.
</details>

<details>
<summary><b>Any other MCP client / MCP Inspector</b></summary>

Point it at the stdio command directly:

```bash
# command:
/absolute/path/to/ins-tunisia-mcp/.venv/bin/python
# args:
-m ins_mcp
```

To explore the tools interactively without an AI client:

```bash
npx @modelcontextprotocol/inspector /absolute/path/to/ins-tunisia-mcp/.venv/bin/python -m ins_mcp
```
</details>

### Run unit tests

```bash
.venv/bin/python -m pytest -m "not live"   # offline, parses saved XML fixtures
.venv/bin/python -m pytest -m live         # opt-in, hits the real INS endpoint
```

---

## Tools

### Discovery

| Tool | What it does |
|---|---|
| **ins_list_sources** | List every source (cube) with its dimensions, declared period, and `has_data`. *"What datasets does the INS portal have?"* |
| **ins_search_sources** | Accent/case-insensitive keyword search over source titles and descriptions. *"Find the national accounts source"* |
| **ins_get_source** | Full detail for one source: typed dimensions, declared period, and `has_data`. *"What breakdowns does the census housing cube have?"* |

### Navigation

| Tool | What it does |
|---|---|
| **ins_get_dimension_elements** | Browse (drill-down by `parent_key`) or keyword-search a dimension's codelist to find the element keys for a query. *"Find the governorate code for Sfax"* |

### Data Query

| Tool | What it does |
|---|---|
| **ins_get_data** | Fetch observations as tidy records — each carries period, frequency, value, unit, scale, and a mandatory source attribution block, plus a coverage summary. *"Population of Tunis governorate in the 2014 census"* |
| **ins_get_availability** | Real data coverage (start/finish years) for a specific selection, distinct from the declared range. *"What years of GDP data are actually available?"* |

### Maintenance

| Tool | What it does |
|---|---|
| **ins_refresh_catalogue** | Re-pull the source catalogue from the live API. *"Refresh the INS catalogue"* |

---

## Example Queries

Just ask in natural language. The AI selects and chains the right tools automatically.

> **"What was the population of Tunis governorate in the 2014 census?"**
> Uses `ins_search_sources` / `ins_get_source` to locate the census cube, `ins_get_dimension_elements` to resolve the governorate and indicator codes, then `ins_get_data` to return the figure with its source attribution.

> **"Show Tunisia's GDP at market prices from 2014 to 2017."**
> Uses `ins_get_dimension_elements` to find the GDP aggregate, then `ins_get_data` with an annual window, returning each year's value in million dinars.

> **"Is quarterly data available for value added by sector of activity?"**
> Uses `ins_get_availability` and a quarterly `ins_get_data` call — observations come back as `"2015-Q1"`, `"2015-Q2"`, …

---

## How it works

A few design choices worth knowing when you query through an AI:

- **Identity is by code, never label.** Dimensions are addressed by their string `Id`, elements by numeric `KEY`. INS labels are dirty (trailing tabs, typos like `Superificie`, French/English drift), so the server resolves the measure dimension by *type*, not name.
- **`has_data: false` flags an empty shell.** Some cubes publish full structure but serve no values (several 2014-census household cross-tabs). `has_data: false` is authoritative "no data served — skip it"; `true` means populated *or* not yet probed since the last reseed. It never wrongly marks a populated cube empty.
- **Declared range ≠ availability.** A source's declared period can be a projection horizon (e.g. 2050). Real coverage comes from the data returned (`coverage`) or `ins_get_availability` per selection.
- **Frequency is explicit.** A date window without a frequency returns a structured error rather than silently widening to the full native range. Observation `period` is a string: `"2015"` (annual) / `"2015-Q1"` (quarterly).

---

## Data Limitations

Working with INS portal data, be aware of:

- **French-dominant, dirty labels** — names carry trailing tabs, typos, and French/English drift; search is substring (accent- and case-insensitive) but not synonym-aware ("surface" won't find "superficie")
- **Empty cubes exist** — some 2014-census household cross-tabs are published as structure-only shells with no values; these are flagged `has_data: false`
- **Declared period is not availability** — for projection sources the finish year is a horizon, not the latest actual data
- **Mixed frequencies** — the catalogue carries at least annual and quarterly sources; semestrial is rejected by the API, and no monthly source is currently confirmed
- **Plain HTTP upstream** — the INS API is served over HTTP; the server hardens its XML parsing for untrusted content

---

## Contributing

Contributions are welcome.

- One feature or fix per pull request
- Use conventional commit messages (`feat:`, `fix:`, `docs:`)
- All submissions are reviewed before merging

---

## Citation

If you use ins-tunisia-mcp in your work, please cite the technical report archived on Zenodo:

> Gasmi, T. (2026). *ins-tunisia-mcp: An MCP Server for Conversational Access to Tunisia's National Statistics.* Zenodo. https://doi.org/10.5281/zenodo.20738616

```bibtex
@misc{gasmi2026instunisiamcp,
  title        = {ins-tunisia-mcp: An MCP Server for Conversational
                  Access to Tunisia's National Statistics},
  author       = {Gasmi, Tarek},
  year         = {2026},
  publisher    = {Zenodo},
  doi          = {10.5281/zenodo.20738616},
  url          = {https://doi.org/10.5281/zenodo.20738616}
}
```

The DOI above is the *concept DOI* — it always resolves to the latest version.

### Data

Data served by ins-tunisia-mcp originates from the Institut National de la Statistique (INS), Tunisie, via [dataportal.ins.tn](http://dataportal.ins.tn), under the INS *conditions d'utilisation* (https://www.ins.tn/conditions-utilisation, governed by Tunisian law). Attribution is mandatory under Article 4: every data-returning tool embeds a source attribution block (producer, source URL, last-updated date, disclaimer) in its response. When publishing results, cite the originating portal:

```bibtex
@misc{ins_tunisia,
  title        = {Portail de données de l'Institut National de la
                  Statistique (INS), Tunisie},
  author       = {{Institut National de la Statistique (INS), Tunisie}},
  year         = {2026},
  url          = {http://dataportal.ins.tn},
  note         = {Conditions d'utilisation: https://www.ins.tn/conditions-utilisation.
                  Data accessed via the ins-tunisia-mcp server. The attribution
                  block returned with each result must be reproduced.}
}
```

See [NOTICE.md](NOTICE.md) for the full attribution and licence notice. This tool is not an official INS publication and is not endorsed by INS.

---

## License

This project is licensed under the [MIT License](LICENSE).

Data sourced from [dataportal.ins.tn](http://dataportal.ins.tn) under the INS *conditions d'utilisation*.
