.PHONY: build test lint lint-go lint-rust fmt fmt-go fmt-rust

GOLANGCI_LINT ?= golangci-lint

build:
	go build ./...

test:
	go test ./...

lint: lint-go lint-rust

lint-go:
	$(GOLANGCI_LINT) run ./...

lint-rust:
	cd pod_wire && cargo clippy -- -D warnings

fmt: fmt-go fmt-rust

fmt-go:
	gofmt -w $$(find . -name '*.go' -not -path './vendor/*')
	goimports -w $$(find . -name '*.go' -not -path './vendor/*')

fmt-rust:
	cd pod_wire && cargo fmt
