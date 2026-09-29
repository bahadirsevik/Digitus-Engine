/**
 * copy-ds-css.mjs — tsc .css dosyalarini kopyalamaz.
 *
 * `tsconfig.ds.json` ile derlenen design-system cikti agacinda (dist-ds/)
 * bilesenlerin `import './X.css'` satirlari duruyor ama dosyalarin kendisi
 * yok; esbuild bundle asamasinda "could not resolve" hatasi verir.
 * Bu script src/ altindaki tum .css dosyalarini ayni goreli yola kopyalar.
 *
 * Kullanim: npm run build:ds  (tsc'den SONRA calisir)
 */
import { cpSync, mkdirSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const PKG = dirname(dirname(fileURLToPath(import.meta.url)))
const SRC = join(PKG, 'src')
const OUT = join(PKG, 'dist-ds')

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (name.endsWith('.css')) out.push(p)
  }
  return out
}

const files = walk(SRC)
for (const file of files) {
  const target = join(OUT, relative(SRC, file))
  mkdirSync(dirname(target), { recursive: true })
  cpSync(file, target)
}
console.error(`copy-ds-css: ${files.length} css -> dist-ds/`)
