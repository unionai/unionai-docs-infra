{{ define "main" }}
{{- /*
Template for generating markdown versions of individual pages.
This renders the content with shortcodes processed for markdown output.
Only emit content for pages allowed in the current variant.
*/ -}}
{{- if (partial "page-allowed.html" .).variant }}
{{- if .Params.description }}

{{ .Params.description }}
{{- end }}

{{ .RawContent }}

---
**Source**: https://github.com/unionai/unionai-docs/blob/main/content/{{ .File.Path }}
**HTML**: https://www.union.ai{{ .Permalink }}
{{- end }}
{{ end }}