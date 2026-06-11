AGENTS.md — operational notes for agents working in this repo

Keep this short. Include only repo-specific facts an agent would likely miss.

Rules
- 总是用中文回复我

Quick rules
- Always run the crawler/tooling from inside the loL_crawler directory. The scripts use top-level module imports (e.g. `from builders.sft_builder import ...`) so `cd loL_crawler && python main.py ...` is the safe invocation. Running `python loL_crawler/main.py` from the repo root will cause ModuleNotFoundError.
- Use `--mock` for any scraper when you do not have valid cookies or to run a fast offline smoke-test. Mock mode produces small, deterministic sample files and avoids external rate limits.

Install
- cd loL_crawler
- python -m pip install -r requirements.txt

Smoke test (fast, zero secrets)
- cd loL_crawler
- python main.py all --mock
  - runs downloader (mock), mock scrapers, preprocess, and SFT builder to produce sample files under `data/`.

Primary commands (examples)
- Run only Tieba (mock or real):
  - cd loL_crawler
  - python main.py tieba --mock
  - python main.py tieba --bduss "<BDUSS>" --fbar kangya --pages 3
- Run only NGA:
  - cd loL_crawler
  - python main.py nga --mock
  - python main.py nga --cookie "<nga_cookie>" --fid -7 --pages 5
- Run only Bilibili danmaku:
  - cd loL_crawler
  - python main.py bili --mock
  - python main.py bili --bvid BV1xxxx --cookie "<bili_cookie>"
- Download Riot Data Dragon (champion/item JSON):
  - cd loL_crawler
  - python main.py datadragon --version 14.1.1
- Preprocess raw -> processed:
  - cd loL_crawler
  - python main.py preprocess
- Build SFT JSONL files (and sft_mix.jsonl):
  - cd loL_crawler
  - python main.py build-sft --max 0   # 0 = unlimited

Where data lands (important file patterns)
- All pipeline data is under loL_crawler/data:
  - data/raw/: raw scraper outputs. Tieba writes `fname_tid_{tid}.jsonl` files; NGA writes `nga_{fid}_threads.jsonl` and `nga_{fid}_posts.jsonl`; Bilibili writes `bili_{bvid}.jsonl` (first line is a meta record with `_meta: True`).
  - data/processed/: cleaned JSONL produced by `preprocess`.
  - data/sft/: per-source SFT files (tieba_sft.jsonl, bili_sft.jsonl, ...) and the merged `sft_mix.jsonl` used for training.
  - data/datadragon/: downloaded Riot static JSON per version (e.g. `en_US-14.1.1/champion.json`).

Correct SFT build order (exact and observable)
1. (Optional) `python main.py datadragon --version <v>` to download DataDragon if you want champion/item samples.
2. Run scrapers that you want (tieba/nga/bili). Use `--mock` for development.
3. `python main.py preprocess` — creates `data/processed/` and demojizes / does deduplication.
4. `python main.py build-sft [--max N]` — produces `data/sft/*.jsonl` and `data/sft/sft_mix.jsonl`.

Secrets and safety
- Tieba requires a BDUSS cookie string; NGA requires login cookie(s). Pass them only on the command line (or via a temporary environment variable) and never commit them. Example: `--bduss "<BDUSS>"` or `--nga-cookie "key=val; ..."`.
- Use `--mock` when testing locally or when you cannot/should not expose credentials.

Common failure modes (what an agent would otherwise guess wrong)
- Module imports fail if you run a script from the repo root. Always `cd loL_crawler` first.
- TiebaCrawler raises a runtime error if `aiotieba` is not installed. Install via requirements or run with `--mock`.
- NGA scraping requires a valid login cookie and is rate-limited / fragile. Expect to provide real cookies; the code parses `read.php?lite=js` payloads with a regex — if NGA changes output the parser will fail.
- Bilibili: proto parsing requires external proto Python module (`danmaku_proto.dm_web_seg_pb2`) + `protobuf` installed; otherwise the code falls back to XML parsing. The repo does not include the proto definitions.
- If `preprocess` has deduped files you don't expect, delete `data/processed/*` to rerun dedup from scratch.

Config and quick edits
- Change crawl targets, concurrency, and page limits in `loL_crawler/config.py` (TIEBA_CONCURRENCY, TIEBA_PN_MAX_* etc.).
- Progress trackers live next to raw outputs: files named `{fname}_threads.done`, `{fname}_posts.done`, `{fname}_comments.done` under `data/raw/`. Delete those `.done` files to re-crawl IDs.
- Update meme/lexicon used by SFT in `loL_crawler/resources/meme_dict.json`. The SFT builder reads that file directly.

Logs
- Per-component logs live in `loL_crawler/logs/` (e.g. `tieba_crawler.log`, `sft_builder.log`). Check them when a step silently produced no output.

Where to read next (highest signal files)
- loL_crawler/README.md  — pipeline overview and usage examples
- loL_crawler/main.py     — orchestrator and exact CLI subcommands
- loL_crawler/spiders/*   — tieba/nga/bili/datadragon scrapers (behavioural details)
- loL_crawler/processors/preprocess.py  — demojize, dedup, stopwords behavior
- loL_crawler/builders/sft_builder.py   — how SFT JSONL samples are composed and merged
- loL_crawler/config.py   — single source for default limits, paths and names
