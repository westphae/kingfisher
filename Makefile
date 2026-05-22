# Kingfisher dev convenience targets.
#
# Linting and formatting cover both languages in the tree:
#   * Go (cmd/, internal/)         golangci-lint + gofmt + goimports
#   * Rust (pod_wire/, firmware/)  cargo fmt + cargo clippy
#
# golangci-lint is fetched on demand into .bin/ so contributors don't
# have to install anything globally.

GO              ?= go
CARGO           ?= cargo
GOLANGCI_LINT   ?= $(CURDIR)/.bin/golangci-lint
GOLANGCI_VERSION ?= v2.12.2

# Rust crates in the workspace. firmware/pod targets an embedded riscv32
# triple, so clippy runs against its native checks only (no target=...).
RUST_CRATES := pod_wire firmware/pod

.PHONY: help
help:
	@echo "Targets:"
	@echo "  make build       — go build ./..."
	@echo "  make test        — go test ./... && cargo test (pod_wire)"
	@echo "  make lint        — full lint: Go + Rust"
	@echo "  make lint-go     — golangci-lint run ./..."
	@echo "  make lint-rust   — cargo fmt --check + cargo clippy (all crates)"
	@echo "  make fmt         — auto-format Go and Rust in place"
	@echo "  make fmt-check   — verify formatting (no writes)"
	@echo "  make tools       — install developer tools into .bin/"

# ----------------------------------------------------------------------
# Build / test

.PHONY: build
build:
	$(GO) build ./...

.PHONY: test
test:
	$(GO) test ./...
	cd pod_wire && $(CARGO) test

# ----------------------------------------------------------------------
# Linting

.PHONY: lint
lint: lint-go lint-rust

.PHONY: lint-go
lint-go: $(GOLANGCI_LINT)
	$(GOLANGCI_LINT) run ./...

.PHONY: lint-rust
lint-rust:
	@for crate in $(RUST_CRATES); do \
	  echo "==> cargo fmt --check ($$crate)"; \
	  (cd $$crate && $(CARGO) fmt --check) || exit $$?; \
	done
	@echo "==> cargo clippy (pod_wire)"
	cd pod_wire && $(CARGO) clippy --all-targets -- -D warnings
	# firmware/pod is a no_std binary for an esp32-c3 target; clippy
	# requires the full esp toolchain (esp-hal, esp-radio, etc.) and a
	# riscv32imc-unknown-none-elf target. Skip it from the standard lint
	# loop — contributors with the firmware toolchain should run
	# `make lint-firmware` from a host that has it installed.

.PHONY: lint-firmware
lint-firmware:
	cd firmware/pod && $(CARGO) clippy --all-targets -- -D warnings

# ----------------------------------------------------------------------
# Formatting

.PHONY: fmt
fmt: $(GOLANGCI_LINT)
	$(GOLANGCI_LINT) fmt
	@for crate in $(RUST_CRATES); do \
	  echo "==> cargo fmt ($$crate)"; \
	  (cd $$crate && $(CARGO) fmt) || exit $$?; \
	done

.PHONY: fmt-check
fmt-check: $(GOLANGCI_LINT)
	$(GOLANGCI_LINT) fmt --diff
	@for crate in $(RUST_CRATES); do \
	  echo "==> cargo fmt --check ($$crate)"; \
	  (cd $$crate && $(CARGO) fmt --check) || exit $$?; \
	done

# ----------------------------------------------------------------------
# Tool installation. golangci-lint is pinned via GOLANGCI_VERSION.

.PHONY: tools
tools: $(GOLANGCI_LINT)

$(GOLANGCI_LINT):
	@mkdir -p $(CURDIR)/.bin
	GOBIN=$(CURDIR)/.bin $(GO) install \
	  github.com/golangci/golangci-lint/v2/cmd/golangci-lint@$(GOLANGCI_VERSION)
