# CLI starters

Each starter implements the same dependency-free protocol:

```text
component-cli greet --name VALUE
component-cli --version
```

Names must be non-empty UTF-8. Arguments are positional and exact; no shell
parsing, environment expansion, or alternate flag syntax is performed. A name
containing spaces or newlines is still a single argument. Success emits one
UTF-8 JSON line on stdout; invalid arguments emit one JSON error line on stderr
and exit with status 2.

Build the selected starter from its directory:

- Go 1.23+: `go build -o component-cli .`
- Rust (edition 2021): `cargo build --release`
- C++17: `cmake -S . -B build`, then `cmake --build build`

The initializer may replace the exact string `component-cli` in the selected
starter to rename its package and executable.

Run the cross-language contract tests with
`python3 -m unittest discover -s tests -p test_cli.py -v`.
Tests build private source copies in scratch directories under `cli-starters/`,
clean them afterwards, and explicitly skip unavailable toolchains.
