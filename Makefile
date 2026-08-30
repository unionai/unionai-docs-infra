# unionai-docs-infra/Makefile — Shared build logic.
# Invoked from the thin top-level Makefile via: make -f unionai-docs-infra/Makefile <target>
# Working directory is always the repo root (not unionai-docs-infra/).
# VERSION, VARIANTS, DEFAULT_VARIANT, REPO_ROOT, PORT are exported by the top-level Makefile.

PREFIX := $(if $(VERSION),docs/$(VERSION),docs)
DEFAULT_VARIANT ?= union
PORT ?= 9000
BUILD := $(shell date +%s)
UV := uv run --project unionai-docs-infra

.PHONY: index-search index-search-settings index-search-synonyms refresh-search-popularity check-search-labels all base dist variant dev serve usage update-examples sync-examples llm-docs check-api-docs update-api-docs regen-api-docs-all check-helm-docs update-helm-docs generate-helm-docs update-redirects dry-run-redirects deploy-redirects check-deleted-pages check-generated-links check-rendered-images check-asset-refs check-version-menu-parity check-pin-window-parity check-links check-generated-content check-icon-names check-subpage-cards update-icon-names clean clean-generated
all: usage

usage:
	@unionai-docs-infra/scripts/make_usage.sh

clean:
	rm -rf dist public

# WARNING: clean-generated removes all generated content (API docs, CLI docs,
# notebooks, YAML data, linkmaps). Do NOT commit after running this without
# regenerating via 'make dist'. CI will block the merge (check-generated-content).
clean-generated: clean
	rm -rf content/_static/notebooks
	@$(UV) unionai-docs-infra/tools/clean_generated.py

base:
	@if ! unionai-docs-infra/scripts/pre-build-checks.sh; then exit 1; fi
	@if ! unionai-docs-infra/scripts/pre-flight.sh; then exit 1; fi
	@echo "Converting Jupyter notebooks..."
	@unionai-docs-infra/tools/jupyter_generator/gen_jupyter.sh
	@# Docs version manifest (DOC-1245, PRD §9.1): a pinned tag build carries a
	@# committed data/version-manifest.json (written by the cut); for /docs/latest
	@# and dev it's absent, so generate it from the current tree. Gated on
	@# versions.toml so it's inert without versioning; best-effort so a resolver
	@# hiccup never fails the build (the version-footer partial just no-ops).
	@if [ -f versions.toml ] && [ ! -f data/version-manifest.json ]; then \
		echo "Generating data/version-manifest.json (versioning on, not a pinned cut)..."; \
		$(UV) unionai-docs-infra/tools/api_generator/manifest.py --write --variant both --out data/version-manifest.json || echo "  (manifest generation skipped)"; \
	fi
	rm -rf dist
	mkdir -p dist
	mkdir -p dist/docs
	cat unionai-docs-infra/index.html.tmpl | sed -e 's#@@BASE@@#/${PREFIX}#g' -e 's#@@DEFAULT_VARIANT@@#$(DEFAULT_VARIANT)#g' -e 's#@@BUILD@@#$(BUILD)#g' > dist/index.html
	cat unionai-docs-infra/index.html.tmpl | sed -e 's#@@BASE@@#/${PREFIX}#g' -e 's#@@DEFAULT_VARIANT@@#$(DEFAULT_VARIANT)#g' -e 's#@@BUILD@@#$(BUILD)#g' > dist/docs/index.html
	@# Root 404.html (DOC-1334 failure-surfaces): its presence switches CF Pages
	@# from SPA-fallback (the 200-stub) to REAL 404s for unknown paths.
	cat unionai-docs-infra/404-root.html.tmpl | sed -e 's#@@BASE@@#/${PREFIX}#g' -e 's#@@DEFAULT_VARIANT@@#$(DEFAULT_VARIANT)#g' -e 's#@@BUILD@@#$(BUILD)#g' > dist/404.html

# Push the built docs into the search index. Runs after a deploy, scoped to the
# slices THIS branch built: main carries v2 + latest, the v1 branch carries v1.
# Nothing outside those slices is touched, so the two lines cannot clobber each
# other. See unionai-docs-infra/tools/algolia_indexer/README.md.
#
# Skips silently without credentials so `make dist` stays runnable locally.
# Settings are NOT applied here -- see index-search-settings.
index-search:
	@if [ -z "$$ALGOLIA_DOCS_2_WRITE_API_KEY" ]; then \
		echo "  skip search index (ALGOLIA_DOCS_2_WRITE_API_KEY unset)"; \
	else \
		records=$$(mktemp "$${TMPDIR:-/tmp}/search-records.XXXXXX") || exit 1; \
		md=$$(mktemp "$${TMPDIR:-/tmp}/markdown-records.XXXXXX") || exit 1; \
		echo "== keyword index (union) =="; \
		$(UV) unionai-docs-infra/tools/algolia_indexer/build_records.py \
			--dist dist --out "$$records" && \
		$(UV) unionai-docs-infra/tools/algolia_indexer/push_records.py \
			--records "$$records" --prune "$$records.prune.json" --index union; \
		status=$$?; \
		if [ $$status -eq 0 ]; then \
			echo "== Ask AI retrieval index (union-markdown) =="; \
			if $(UV) unionai-docs-infra/tools/algolia_indexer/build_markdown_records.py \
				--dist dist --out "$$md" && \
			   $(UV) unionai-docs-infra/tools/algolia_indexer/push_records.py \
				--records "$$md" --prune "$$md.prune.json" --index union-markdown; then \
				echo "  Ask AI retrieval index updated"; \
			elif [ -n "$$(sed -n 's/^ask_ai_key *= *"\(..*\)"/\1/p' unionai-docs-infra/hugo.toml 2>/dev/null | head -1)" ]; then \
				echo "  ****************************************************************"; \
				echo "  ERROR: the Ask AI retrieval index was NOT updated, and Ask AI"; \
				echo "  IS LIVE (ask_ai_key is set in hugo.toml). Readers would be"; \
				echo "  answered from a corpus that is stale against this build, with"; \
				echo "  nothing on the page saying so. Failing the deploy."; \
				echo "  ****************************************************************"; \
				status=1; \
			else \
				echo "  ****************************************************************"; \
				echo "  WARNING: the Ask AI retrieval index was NOT updated."; \
				echo "  The union-markdown corpus is now STALE against this build."; \
				echo "  Continuing because ask_ai_key is NOT set, so Ask AI is not"; \
				echo "  serving readers and nothing in production reads this corpus."; \
				echo "  This becomes fatal automatically once the key is set."; \
				echo "  ****************************************************************"; \
			fi; \
		fi; \
		rm -f "$$records" "$$md" "$$records.prune.json" "$$md.prune.json"; \
		exit $$status; \
	fi

# Index settings are config-as-code but applied deliberately, not on every
# deploy: a push that also rewrote settings would silently revert any dashboard
# tuning someone did to diagnose a relevance problem.
index-search-settings:
	@$(UV) unionai-docs-infra/tools/algolia_indexer/push_records.py \
		--index union --settings unionai-docs-infra/tools/algolia_indexer/settings.json
	@$(UV) unionai-docs-infra/tools/algolia_indexer/push_records.py \
		--index union-markdown \
		--settings unionai-docs-infra/tools/algolia_indexer/settings.markdown.json
# The search-quality benchmark's answer key references real content URLs. A PR
# that renames or deletes a labelled page silently rots the benchmark, so the
# next eval reports a "ranking regression" that is really labelling decay.
# Fail loudly at PR time instead. Cheap: pure path existence, no network.
check-search-labels:
	@$(UV) unionai-docs-infra/tools/algolia_indexer/check_labels.py \
		unionai-docs-infra/tools/algolia_indexer/queries.judged.json \
		--content content

# Synonyms, like settings, are applied DELIBERATELY -- never on a deploy.
# push_records uses replaceExistingSynonyms=true, so the payload must always be
# complete: pushing one source file alone would delete the other's synonyms.
# merge_synonyms.py is what makes it complete (and drops the findings buckets,
# which name queries with no page to point at).
index-search-synonyms:
	@$(UV) unionai-docs-infra/tools/algolia_indexer/merge_synonyms.py \
		--draft unionai-docs-infra/tools/algolia_indexer/synonyms.draft.json \
		--manual unionai-docs-infra/tools/algolia_indexer/synonyms.manual.json \
		--out unionai-docs-infra/tools/algolia_indexer/synonyms.merged.json
	@$(UV) unionai-docs-infra/tools/algolia_indexer/push_records.py \
		--index union \
		--synonyms unionai-docs-infra/tools/algolia_indexer/synonyms.merged.json

# Popularity prior feeding weight.pageRank. Refreshed DELIBERATELY, not per
# deploy: click data moves slowly, and a prior that shifts under every build
# makes a ranking regression impossible to attribute. Reads the LEGACY app by
# default -- it holds the history; pass --app-env ALGOLIA_DOCS_2 once the new
# app has accumulated its own. Commit the resulting popularity.json.
refresh-search-popularity:
	@$(UV) unionai-docs-infra/tools/algolia_indexer/fetch_popularity.py \
		--out unionai-docs-infra/tools/algolia_indexer/popularity.json

dist:
	@VARIANTS="$(VARIANTS)" PARALLEL_HUGO="$(PARALLEL_HUGO)" unionai-docs-infra/scripts/build_dist.sh

variant:
	@if [ -z ${VARIANT} ]; then echo "VARIANT is not set"; exit 1; fi
	@VERSION=${VERSION} BUILD=${BUILD} unionai-docs-infra/scripts/run_hugo.sh
	@VERSION=${VERSION} VARIANT=${VARIANT} PREFIX=${PREFIX} BUILD=${BUILD} unionai-docs-infra/scripts/gen_404.sh
	@if [ -d "dist/docs/${VERSION}/${VARIANT}/tmp-md" ]; then \
		$(UV) unionai-docs-infra/tools/llms_generator/process_shortcodes.py \
			--variant=${VARIANT} \
			--version=${VERSION} \
			--input-dir=dist/docs/${VERSION}/${VARIANT}/tmp-md \
			--output-dir=dist/docs/${VERSION}/${VARIANT} \
			--base-path=. \
			--quiet; \
		rm -rf dist/docs/${VERSION}/${VARIANT}/tmp-md; \
	fi

dev:
	@if ! unionai-docs-infra/scripts/pre-flight.sh; then exit 1; fi
	@if ! unionai-docs-infra/scripts/dev-pre-flight.sh; then exit 1; fi
	rm -rf public
	hugo server --config unionai-docs-infra/hugo.toml,unionai-docs-infra/hugo.site.toml,unionai-docs-infra/hugo.ver.toml,unionai-docs-infra/hugo.dev.toml,hugo.local.toml

serve:
	@if [ ! -d dist ]; then echo "Run 'make dist' first"; exit 1; fi
	@PORT=${PORT} LAUNCH=${LAUNCH} unionai-docs-infra/scripts/serve.sh

update-examples:
	git submodule update --remote

init-examples:
	git submodule update --init

check-jupyter:
	unionai-docs-infra/tools/jupyter_generator/check_jupyter.sh

check-images:
	unionai-docs-infra/scripts/check_images.sh

validate-urls:
	@echo "Validating URLs across all variants..."
	@for variant in $(VARIANTS); do \
		echo "Checking $$variant..."; \
		if [ -d "dist/docs/${VERSION}/$$variant" ]; then \
			$(UV) python3 unionai-docs-infra/tools/validate_urls.py dist/docs/${VERSION}/$$variant; \
		else \
			echo "No processed markdown found for $$variant"; \
		fi \
	done

url-stats:
	@echo "URL statistics across all variants:"
	@for variant in $(VARIANTS); do \
		echo "=== $$variant ==="; \
		if [ -d "dist/docs/${VERSION}/$$variant" ]; then \
			$(UV) python3 unionai-docs-infra/tools/validate_urls.py dist/docs/${VERSION}/$$variant --stats; \
		else \
			echo "No processed markdown found for $$variant"; \
		fi \
	done

llm-docs:
	@VERSION=${VERSION} $(UV) unionai-docs-infra/tools/llms_generator/build_llm_docs.py --no-make-dist --quiet

update-redirects:
	@echo "Detecting moved pages and appending to redirects.csv..."
	@$(UV) unionai-docs-infra/tools/redirect_generator/detect_moved_pages.py

dry-run-redirects:
	@echo "Dry run: detecting moved pages from git history..."
	@$(UV) unionai-docs-infra/tools/redirect_generator/detect_moved_pages.py --dry-run

deploy-redirects:
	@$(UV) unionai-docs-infra/tools/redirect_generator/deploy_redirects.py

check-deleted-pages:
	@$(UV) unionai-docs-infra/tools/redirect_generator/check_deleted_pages.py

check-links:
	@$(UV) unionai-docs-infra/tools/link_checker/check_internal_links.py

# Checks the links the GENERATOR writes into dist/, which check-links does not
# see: it reads content/. Needs `make dist` first. The baseline file is a
# ratchet -- it holds the links that predate the gate, each attributed to a
# ticket, and shrinks. See tools/link_checker/generated-links-baseline.txt.
check-generated-links:
	@$(UV) unionai-docs-infra/tools/link_checker/check_generated_links.py \
		--exclude unionai-docs-infra/tools/link_checker/generated-links-baseline.txt

# Checks the images the BUILT site actually serves, resolved the way a browser
# does -- against the page's URL, not the source file's directory. Needs
# `make dist` first. check-images reads content/ and cannot see this: a src can
# be correct in the markdown and wrong in the HTML, because Hugo's render hooks
# rebase it. DOC-1515.
check-rendered-images:
	@$(UV) unionai-docs-infra/tools/image_checker/check_rendered_images.py

check-generated-content:
	@$(UV) unionai-docs-infra/tools/check_generated_content.py

# Every `icon="..."` must exist in the set its shortcode resolves against:
# Bootstrap Icons for the <sl-icon> shortcodes, gemoji aliases for `dropdown`.
# The two are mutually unintelligible and both fail SILENTLY (an empty slot, or
# a literal `:name:`), and no other check can see it -- a missing icon is not a
# broken link. DOC-1444.
# A section page with children must say where its subpage cards go, or opt out.
# The cards are placed by an explicit shortcode rather than injected (DOC-1509),
# so this is what stops a new section page shipping with no way forward for a
# reader. The baseline holds the pages that predate the gate; it shrinks.
check-subpage-cards:
	@$(UV) unionai-docs-infra/tools/check_subpage_cards.py \
		--baseline unionai-docs-infra/tools/check_subpage_cards_baseline.txt

check-icon-names:
	@$(UV) unionai-docs-infra/tools/check_icon_names.py

# Refresh the vendored icon list after a Shoelace version bump.
update-icon-names:
	@$(UV) unionai-docs-infra/tools/check_icon_names.py --update

check-api-docs:
	@$(UV) unionai-docs-infra/tools/api_generator/check_versions.py --check

# Advisory. Audits the GENERATED API reference for reader-visible defects
# (literal RST, language-less code blocks, empty parameter descriptions, ...).
# Set SDK_SOURCE to a flyte-sdk checkout to split empty descriptions into
# "the generator dropped documented prose" vs "nothing in source to render".
check-api-docs-rendered:
	@$(UV) unionai-docs-infra/tools/check_generated_api_docs.py $(if $(SDK_SOURCE),--source $(SDK_SOURCE),)

# Fails when a plugin-provided CLI command is published to the wrong variants,
# e.g. a Union-only command rendered into the open-source docs (DOC-1479).
# Reads what each distribution registered from the API venv's entry points, so
# it cannot inherit a misclassification made by the generator itself.
check-cli-variant-gating:
	@$(UV) unionai-docs-infra/tools/check_cli_variant_gating.py

check-asset-refs:
	@unionai-docs-infra/scripts/check-asset-refs.sh

# The version selector's entries are built twice (baked by run_hugo.sh, published
# per-line by build_versions.sh for sibling pages to fetch). Assert they agree.
check-version-menu-parity:
	@unionai-docs-infra/scripts/check-version-menu-parity.sh

# The pin-retention window is stated in three places that cannot share a
# constant (two argparse defaults and a Hugo template). DOC-1441.
check-pin-window-parity:
	@unionai-docs-infra/scripts/check-pin-window-parity.sh

update-api-docs:
	@$(UV) unionai-docs-infra/tools/api_generator/check_versions.py --update

# Regenerate every package regardless of version. Needed after a change to the
# GENERATOR, which no version bump would otherwise pick up: the pages are
# committed, and regeneration is triggered by the PyPI version moving.
regen-api-docs-all:
	@$(UV) unionai-docs-infra/tools/api_generator/check_versions.py --all

check-helm-docs:
	@$(UV) unionai-docs-infra/tools/helm_generator/check_helm_versions.py --check

update-helm-docs:
	@$(UV) unionai-docs-infra/tools/helm_generator/check_helm_versions.py --update

generate-helm-docs:
	@unionai-docs-infra/tools/helm_generator/generate_helm_docs.sh
