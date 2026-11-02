import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const sourcePath = resolve(root, 'README.md')
const authEndpoint = process.env.QUANTDINGER_TRANSLATE_AUTH_ENDPOINT ||
  'https://edge.microsoft.com/translate/auth'
const endpoint = process.env.QUANTDINGER_TRANSLATE_ENDPOINT ||
  'https://api-edge.cognitive.microsofttranslator.com/translate'

const locales = [
  { code: 'zh-Hant', file: 'README_TW.md', label: '繁體中文' },
  { code: 'ja', file: 'README_JA.md', label: '日本語' },
  { code: 'ko', file: 'README_KO.md', label: '한국어' },
  { code: 'de', file: 'README_DE.md', label: 'Deutsch' },
  { code: 'fr', file: 'README_FR.md', label: 'Français' },
  { code: 'ru', file: 'README_RU.md', label: 'Русский' },
  { code: 'ar', file: 'README_AR.md', label: 'العربية' },
  { code: 'th', file: 'README_TH.md', label: 'ไทย' },
  { code: 'vi', file: 'README_VI.md', label: 'Tiếng Việt' }
]

const languageLinks = [
  { code: 'en', file: 'README.md', label: 'English' },
  { code: 'zh-Hans', file: 'README_CN.md', label: '简体中文' },
  ...locales
]

const selectorPattern = /<p>\s*\n\s*<a href="README\.md">(?:<strong>)?English(?:<\/strong>)?<\/a>[\s\S]*?<\/p>/
const selectorMarker = '<!-- QD_LANGUAGE_SELECTOR -->'
const protectedPattern = /<code\b[^>]*>.*?<\/code>|<[^>\n]+>|`[^`\n]+`|\]\((?:[^()\s]|\([^)]*\))+(?:\s+"[^"]*")?\)|https?:\/\/[A-Za-z0-9][A-Za-z0-9./?&=_:%#@+~,-]*[A-Za-z0-9/#]|\{[^{}\n]+\}|%\([^)]+\)[sdif](?![A-Za-z])|%[sdif](?![A-Za-z])|\b(?:QuantDinger|OpenByteInc|JEV|MCP|OpenAPI|GitHub|Docker|Compose|PostgreSQL|Redis|Kafka|Flask|Gunicorn|Celery|Prometheus|Grafana|Alertmanager|Nginx|Python|Node\.js|PowerShell|Linux|macOS|Windows|Binance|OKX|Bitget|Bybit|Gate|HTX|IBKR|Alpaca|OpenRouter|OpenAI|Google|DeepSeek|Grok|MiniMax)\b/g
const delay = milliseconds => new Promise(resolvePromise => setTimeout(resolvePromise, milliseconds))

let authToken = ''

function languageSelector(activeCode) {
  const links = languageLinks.map(locale => {
    const link = `<a href="${locale.file}">${locale.label}</a>`
    return locale.code === activeCode ? `<strong>${link}</strong>` : link
  })
  return `<p>\n  ${links.join(' ·\n  ')}\n</p>`
}

function protectText(value) {
  const protectedValues = []
  const text = value.replace(protectedPattern, match => {
    const token = `__QD${String(protectedValues.length).padStart(6, '0')}__`
    protectedValues.push(match)
    return token
  })
  return { text, protectedValues }
}

function restoreText(value, protectedValues) {
  let restored = value
  protectedValues.forEach((original, index) => {
    const token = `__QD${String(index).padStart(6, '0')}__`
    if (!restored.includes(token)) {
      throw new Error(`Translation response lost protected token ${token}`)
    }
    restored = restored.split(token).join(original)
  })
  return restored.trim()
}

async function getAuthToken(forceRefresh = false) {
  if (authToken && !forceRefresh) return authToken
  const response = await fetch(authEndpoint)
  if (!response.ok) {
    const error = new Error(`Translation auth failed with HTTP ${response.status}`)
    error.status = response.status
    throw error
  }
  authToken = (await response.text()).trim()
  if (!authToken) throw new Error('Translation auth returned an empty token')
  return authToken
}

async function translateBatch(values, attempt = 1) {
  const protectedItems = values.map(protectText)
  const query = new URLSearchParams({
    'api-version': '3.0',
    from: 'en',
    textType: 'plain'
  })
  locales.forEach(locale => query.append('to', locale.code))

  try {
    const token = await getAuthToken()
    const response = await fetch(`${endpoint}?${query}`, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${token}`,
        'content-type': 'application/json;charset=UTF-8'
      },
      body: JSON.stringify(protectedItems.map(item => ({ Text: item.text })))
    })
    if (!response.ok) {
      const error = new Error(`Translation request failed with HTTP ${response.status}`)
      error.status = response.status
      throw error
    }
    const payload = await response.json()
    if (!Array.isArray(payload) || payload.length !== values.length) {
      throw new Error('Translation response item count mismatch')
    }
    return payload.map((item, itemIndex) => {
      const translations = {}
      for (const translation of item.translations || []) {
        translations[translation.to] = restoreText(
          translation.text || '',
          protectedItems[itemIndex].protectedValues
        )
      }
      return translations
    })
  } catch (error) {
    if (attempt >= 7) throw error
    if (error.status === 401 || error.status === 403) await getAuthToken(true)
    const baseDelay = error.status === 429 ? 5000 : 1000
    await delay(Math.min(baseDelay * (2 ** (attempt - 1)), 45000))
    return translateBatch(values, attempt + 1)
  }
}

function isFence(line) {
  return /^\s*```/.test(line)
}

function isTableDelimiter(line) {
  return /^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/.test(line)
}

function isTableRow(line) {
  return /^\s*\|.*\|\s*$/.test(line)
}

function splitTableRow(line) {
  const leading = line.match(/^\s*/)?.[0] || ''
  const trimmed = line.trim()
  return {
    leading,
    cells: trimmed.slice(1, -1).split('|').map(cell => cell.trim())
  }
}

function isStructuralLine(line) {
  return !line.trim() ||
    isFence(line) ||
    /^\s*#{1,6}\s+/.test(line) ||
    /^\s*>/.test(line) ||
    /^\s*(?:[-*+] |\d+\. )/.test(line) ||
    /^\s*<[^>]+>/.test(line) ||
    /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line) ||
    isTableRow(line)
}

function parseDocument(source) {
  const normalized = source.replace(/\r\n/g, '\n').replace(selectorPattern, selectorMarker)
  const lines = normalized.split('\n')
  const parts = []
  const entries = []
  let index = 0

  const addText = (sourceText, render) => {
    const entryIndex = entries.length
    entries.push(sourceText)
    parts.push({ type: 'text', entryIndex, render })
  }

  while (index < lines.length) {
    const line = lines[index]

    if (line === selectorMarker) {
      parts.push({ type: 'selector' })
      index += 1
      continue
    }

    if (isFence(line)) {
      const block = [line]
      index += 1
      while (index < lines.length) {
        block.push(lines[index])
        if (isFence(lines[index])) {
          index += 1
          break
        }
        index += 1
      }
      parts.push({ type: 'raw', value: block.join('\n') })
      continue
    }

    if (!line.trim() || /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      parts.push({ type: 'raw', value: line })
      index += 1
      continue
    }

    if (isTableRow(line)) {
      if (isTableDelimiter(line)) {
        parts.push({ type: 'raw', value: line })
      } else {
        const { leading, cells } = splitTableRow(line)
        const start = entries.length
        cells.forEach(cell => entries.push(cell))
        parts.push({ type: 'table', leading, start, count: cells.length })
      }
      index += 1
      continue
    }

    const heading = line.match(/^(\s*#{1,6}\s+)(.*)$/)
    if (heading) {
      addText(heading[2], translated => `${heading[1]}${translated}`)
      index += 1
      continue
    }

    if (/^\s*>/.test(line)) {
      const quoteLines = []
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, '').trim())
        index += 1
      }
      addText(quoteLines.join(' '), translated => `> ${translated}`)
      continue
    }

    const listMatch = line.match(/^(\s*(?:[-*+] |\d+\. ))(.*)$/)
    if (listMatch) {
      const body = [listMatch[2].trim()]
      index += 1
      while (index < lines.length && lines[index].trim() && !isStructuralLine(lines[index])) {
        body.push(lines[index].trim())
        index += 1
      }
      addText(body.join(' '), translated => `${listMatch[1]}${translated}`)
      continue
    }

    if (/^\s*<[^>]+>/.test(line)) {
      const htmlLines = [line]
      index += 1
      while (index < lines.length && lines[index].trim()) {
        htmlLines.push(lines[index])
        index += 1
      }
      const html = htmlLines.join('\n')
      addText(html, translated => translated)
      continue
    }

    const paragraph = [line.trim()]
    index += 1
    while (index < lines.length && !isStructuralLine(lines[index])) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    addText(paragraph.join(' '), translated => translated)
  }

  return { parts, entries }
}

function makeBatches(entries) {
  const batches = []
  let current = []
  let characters = 0
  for (let index = 0; index < entries.length; index += 1) {
    const size = entries[index].length + 20
    if (current.length && (current.length >= 80 || characters + size > 30000)) {
      batches.push(current)
      current = []
      characters = 0
    }
    current.push({ index, value: entries[index] })
    characters += size
  }
  if (current.length) batches.push(current)
  return batches
}

function renderDocument(parts, translatedEntries, localeCode) {
  const rendered = []
  for (const part of parts) {
    if (part.type === 'raw') {
      rendered.push(part.value)
    } else if (part.type === 'selector') {
      rendered.push(languageSelector(localeCode))
    } else if (part.type === 'text') {
      rendered.push(part.render(translatedEntries[part.entryIndex]))
    } else if (part.type === 'table') {
      const cells = translatedEntries.slice(part.start, part.start + part.count)
      rendered.push(`${part.leading}| ${cells.join(' | ')} |`)
    }
  }
  return `${rendered.join('\n').replace(/\n{3,}/g, '\n\n').trim()}\n`
}

function countMatches(value, pattern) {
  return [...value.matchAll(pattern)].length
}

function validateDocument(source, translated, locale) {
  const checks = [
    ['fenced code blocks', countMatches(source, /^```/gm), countMatches(translated, /^```/gm)],
    ['headings', countMatches(source, /^#{1,6}\s+/gm), countMatches(translated, /^#{1,6}\s+/gm)],
    ['images', countMatches(source, /<img\b/g), countMatches(translated, /<img\b/g)],
    ['language links', languageLinks.length, countMatches(translated, /href="README(?:_[A-Z]+)?\.md"/g)]
  ]
  for (const [name, expected, actual] of checks) {
    if (expected !== actual) {
      throw new Error(`${locale.file}: ${name} mismatch (${actual}, expected ${expected})`)
    }
  }

  const sourceUrls = [...source.matchAll(/https?:\/\/[^\s)"<>]+/g)].map(match => match[0]).sort()
  const translatedUrls = [...translated.matchAll(/https?:\/\/[^\s)"<>]+/g)].map(match => match[0]).sort()
  if (JSON.stringify(sourceUrls) !== JSON.stringify(translatedUrls)) {
    throw new Error(`${locale.file}: URL set changed during translation`)
  }
}

async function main() {
  const source = await readFile(sourcePath, 'utf8')
  const { parts, entries } = parseDocument(source)
  const translatedByLocale = Object.fromEntries(
    locales.map(locale => [locale.code, Array(entries.length)])
  )
  const batches = makeBatches(entries)

  console.log(`Translating ${entries.length} README segments in ${batches.length} batches`)
  let completed = 0
  for (const batch of batches) {
    const matrix = await translateBatch(batch.map(entry => entry.value))
    batch.forEach((entry, batchIndex) => {
      for (const locale of locales) {
        const value = matrix[batchIndex][locale.code]
        if (typeof value !== 'string') {
          throw new Error(`${locale.code}: missing translation for segment ${entry.index}`)
        }
        translatedByLocale[locale.code][entry.index] = value
      }
    })
    completed += batch.length
    console.log(`Translated ${completed}/${entries.length} segments`)
    await delay(200)
  }

  for (const locale of locales) {
    const translated = renderDocument(parts, translatedByLocale[locale.code], locale.code)
    validateDocument(source, translated, locale)
    await writeFile(resolve(root, locale.file), translated, 'utf8')
    console.log(`Wrote ${locale.file}`)
  }
}

await main()
