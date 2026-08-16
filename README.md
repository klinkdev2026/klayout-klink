# klink documentation site

Bilingual (EN/zh-CN) static documentation for [klayout-klink](https://github.com/klinkdev2026/klayout-klink).
Deployed via GitHub Pages: https://klinkdev2026.github.io/klayout-klink/

## Deploying

Mirror the source tree, protecting the files below — a plain `/MIR` deletes or
overwrites every one of them:

```bat
robocopy D:\klink_website D:\klink_ghpages_wt /MIR /XD .git ^
         /XF CNAME README.md google9c07de3cf41e6f81.html
```

## Permanent root files — keep when regenerating the site

These files must survive any site rebuild or force-push. If the site is ever
regenerated from scratch, copy them back into the new tree before pushing:

- `CNAME` — the custom domain (klayout-klink.top). Added through the GitHub
  web UI, so it does NOT exist in the D:\klink_website source tree.
- THIS `README.md` — the deploy tree's own file; the source tree has a
  DIFFERENT README that must never overwrite it.
- `google9c07de3cf41e6f81.html` — Google Search Console ownership verification.
  Deleting it drops the Search Console property (sitemap submissions, index
  status) for this site.
- `robots.txt`, `sitemap.xml` — search-engine crawling entry points; regenerate
  `sitemap.xml` if pages changed, but never ship a tree without one.
- `.nojekyll` — required so GitHub Pages serves the tree as-is.
