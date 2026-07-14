# klink documentation site

Bilingual (EN/zh-CN) static documentation for [klayout-klink](https://github.com/klinkdev2026/klayout-klink).
Deployed via GitHub Pages: https://klinkdev2026.github.io/klayout-klink/

## Permanent root files — keep when regenerating the site

These files must survive any site rebuild or force-push. If the site is ever
regenerated from scratch, copy them back into the new tree before pushing:

- `google9c07de3cf41e6f81.html` — Google Search Console ownership verification.
  Deleting it drops the Search Console property (sitemap submissions, index
  status) for this site.
- `robots.txt`, `sitemap.xml` — search-engine crawling entry points; regenerate
  `sitemap.xml` if pages changed, but never ship a tree without one.
- `.nojekyll` — required so GitHub Pages serves the tree as-is.
