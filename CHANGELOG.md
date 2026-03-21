# Changelog

All notable changes to Carryall will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial four-repo restructure (2026-03-21)
- authority-runtime-python: core IAM library with Ed25519 signing, OPA policy engine, envelope system
- Mayor: ClawRouter executive routing engine
- Context system: DAG-backed persistence, compaction, embeddings, vault-scoped ACLs
- Sentinel: adversarial scoring engine for audit events (BLOCK/FLAG/PASS)
- Argus: security scanner with data locality checks
- Notification library with hybrid routing (ntfy for sensitive, Telegram for non-sensitive)
- OPA Rego policy templates for 7 vault domains
- Metadata schema for vault documents
- 178 tests across compiler, MCP server, CLI, roles, SLOS backend
