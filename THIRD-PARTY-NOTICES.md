# HGLOS distribution and design references

The maintained HGLOS code and documentation are distributed under the adjacent
MIT LICENSE. The copyright name JH follows the existing plugin author metadata.
This notice records the scope of the distribution review, not a legal certificate
or proof of ownership of every historical file in the development repository.

## Material included in the maintained packages

The dashboard's HTML, CSS, and SVG implementation is authored for HGLOS.
No upstream dashboard code, template, stylesheet, icon, image, font file,
chart library, or JavaScript runtime is included from the references below.
System font family names refer to fonts installed on the viewer's computer;
no font files are redistributed. Python, Git, and host CLIs remain separate
runtime prerequisites where a skill uses them.

## Design references, not vendored components

The following projects informed visual hierarchy, compact cards, dependency
diagrams, and summary-to-detail navigation. Links do not place upstream code
under HGLOS's MIT license.

| Reference | Observed upstream license | Reference use |
|---|---|---|
| [Tabler](https://github.com/tabler/tabler/blob/dev/LICENSE) | MIT | Summary cards and steps |
| [shadcn/ui](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md) | MIT | Spacing and summary/detail hierarchy |
| [AdminLTE](https://github.com/ColorlibHQ/AdminLTE/blob/master/LICENSE) | MIT | Information cards |
| [Refine](https://github.com/refinedev/refine/blob/main/LICENSE) | MIT for the referenced community repository | Overview layout |
| [react-admin](https://github.com/marmelab/react-admin/blob/master/LICENSE.md) | MIT | Pending-action lists |
| [Plane](https://github.com/makeplane/plane/blob/preview/LICENSE.txt) | AGPL-3.0 | Distinguishing relation types; no code copied |
| [AppFlowy](https://github.com/AppFlowy-IO/AppFlowy/blob/main/LICENSE) | AGPL-3.0 | Multiple projections of one model; no code copied |
| [Grafana](https://github.com/grafana/grafana/blob/main/LICENSE) | AGPL-3.0-only, with upstream exceptions | Node/edge presentation; no code copied |
| [NocoDB](https://github.com/nocodb/nocodb/blob/develop/LICENSE.md) | Sustainable Use License 1.0 | Compact-card/detail pattern; no code copied |
| [Superset](https://github.com/apache/superset/blob/master/LICENSE.txt) | Apache-2.0 | Summary-to-detail exploration; no code copied |

Earlier skill design references are documented in
`skills/hglos-dashboard/references/visual-patterns.md` in the plugin, or
`references/visual-patterns.md` inside the dashboard skill. They also describe
idea-level references rather than bundled upstream implementations.

License labels are a record of the 2026-10-23 source review. Upstream licenses
and commercial editions can change. Adding an upstream implementation or asset
requires checking that exact material and preserving its applicable notices;
the current absence of vendored components must not be assumed after such changes.

## Public artifact boundary

Use the clean `hglos-public-source.zip` or the skill packages produced by the
packaging tools. The public-source allowlist excludes local validation logs,
fixtures, backups, Codex transcripts, legacy work folders, and Git history.
Changing the visibility of the original development repository would expose
tracked files and history outside that allowlist and has not been authorized
or performed by these tools.
