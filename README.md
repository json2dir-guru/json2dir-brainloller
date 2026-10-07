# json2dir-brainloller

json2dir in Brainloller: `json2dir.png` (352x350 pixels) is the pure Brainfuck evaluator of [json2dir-brainfuck](https://github.com/json2dir-guru/json2dir-brainfuck) (`json2dir.bf`, from `json2dir.bfl` by `tools/bfl.py`) laid out by `tools/bf2loller.py` as a boustrophedon snake with rotation pixels at the row ends; it checks UTF-8, parses and validates the JSON and prints a shell script, which the POSIX sh launcher `json2dir` runs (stdin framed as decimal byte values by `od`).

    python3 tools/bf2loller.py json2dir.bf json2dir.png
    JSON2DIR_BL="/path/to/Brainfunk/bfk --lang=loller" ./json2dir < tree.json
