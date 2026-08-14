# unionai-docs-infra/Makefile — Shared build logic.
# Invoked from the thin top-level Makefile via: make -f unionai-docs-infra/Makefile <target>
# Working directory is always the repo root (not unionai-docs-infra/).
# VERSION, VARIANTS, DEFAULT_VARIANT, REPO_ROOT, PORT are exported by the top-level Makefile.

PREFIX := $(if $(VERSION),docs/$(VERSION),docs)
DEFAULT_VARIANT ?= union
PORT ?= 9000
BUILD := $(shell date +%s)
UV := uv run --project unionai-docs-infra

.PHONY: index-search index-search-settings index-search-synonyms refresh-search-popularity check-search-labels all base dist variant dev serve usage update-examples sync-examples llm-docs check-api-docs update-api-docs check-helm-docs update-helm-docs generate-helm-docs update-redirects dry-run-redirects deploy-redirects check-deleted-pages check-asset-refs check-version-menu-parity check-links check-generated-content clean clean-generated
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
			--records "$$records" --index union && \
		echo "== Ask AI retrieval index (union-markdown) ==" && \
		$(UV) unionai-docs-infra/tools/algolia_indexer/build_markdown_records.py \
			--dist dist --out "$$md" && \
		$(UV) unionai-docs-infra/tools/algolia_indexer/push_records.py \
			--records "$$md" --index union-markdown; \
		status=$$?; \
		rm -f "$$records" "$$md"; \
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

check-generated-content:
	@$(UV) unionai-docs-infra/tools/check_generated_content.py

check-api-docs:
	@$(UV) unionai-docs-infra/tools/api_generator/check_versions.py --check

# Advisory. Audits the GENERATED API reference for reader-visible defects
# (literal RST, language-less code blocks, empty parameter descriptions, ...).
# Set SDK_SOURCE to a flyte-sdk checkout to split empty descriptions into
# "the generator dropped documented prose" vs "nothing in source to render".
check-api-docs-rendered:
	@$(UV) unionai-docs-infra/tools/check_generated_api_docs.py $(if $(SDK_SOURCE),--source $(SDK_SOURCE),)

check-asset-refs:
	@unionai-docs-infra/scripts/check-asset-refs.sh

# The version selector's entries are built twice (baked by run_hugo.sh, published
# per-line by build_versions.sh for sibling pages to fetch). Assert they agree.
check-version-menu-parity:
	@unionai-docs-infra/scripts/check-version-menu-parity.sh

update-api-docs:
	@$(UV) unionai-docs-infra/tools/api_generator/check_versions.py --update

check-helm-docs:
	@$(UV) unionai-docs-infra/tools/helm_generator/check_helm_versions.py --check

update-helm-docs:
	@$(UV) unionai-docs-infra/tools/helm_generator/check_helm_versions.py --update

generate-helm-docs:
	@unionai-docs-infra/tools/helm_generator/generate_helm_docs.sh
