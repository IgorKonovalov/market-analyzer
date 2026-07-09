/**
 * i18n foundation unit tests (Plan 0069 phase 1, ADR-0063).
 *
 * jsdom provides localStorage + documentElement, and Node ships full ICU, so
 * `Intl.PluralRules('ru')` resolves Russian's three categories natively.
 */
import { applyLocale, formatMessage, getStoredLocale, setLocale, subscribeLocale, t } from './i18n'

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('lang')
})

describe('getStoredLocale', () => {
  it('defaults to en when no preference is stored', () => {
    expect(getStoredLocale()).toBe('en')
  })

  it('reads a stored explicit locale', () => {
    window.localStorage.setItem('ma.locale', 'ru')
    expect(getStoredLocale()).toBe('ru')
  })

  it('falls back to en for a malformed stored value', () => {
    window.localStorage.setItem('ma.locale', 'banana')
    expect(getStoredLocale()).toBe('en')
  })
})

describe('setLocale / applyLocale', () => {
  it('setLocale persists to localStorage AND sets <html lang>', () => {
    setLocale('ru')
    expect(window.localStorage.getItem('ma.locale')).toBe('ru')
    expect(document.documentElement.lang).toBe('ru')
  })

  it('persists across a reload (a fresh read sees the stored locale)', () => {
    setLocale('ru')
    expect(getStoredLocale()).toBe('ru')
    setLocale('en')
    expect(getStoredLocale()).toBe('en')
  })

  it('applyLocale is DOM-only and does not touch storage', () => {
    applyLocale('ru')
    expect(document.documentElement.lang).toBe('ru')
    expect(window.localStorage.getItem('ma.locale')).toBeNull()
  })
})

describe('subscribeLocale', () => {
  it('fires on an explicit setLocale', () => {
    const cb = jest.fn()
    const unsub = subscribeLocale(cb)
    setLocale('ru')
    expect(cb).toHaveBeenCalledTimes(1)
    expect(cb).toHaveBeenCalledWith('ru')
    unsub()
  })

  it('stops firing after unsubscribe', () => {
    const cb = jest.fn()
    const unsub = subscribeLocale(cb)
    unsub()
    setLocale('ru')
    expect(cb).not.toHaveBeenCalled()
  })
})

describe('t', () => {
  it('resolves a catalog key in the default locale', () => {
    expect(t('settings.appearance.language.label')).toBe('Language')
  })

  it('falls back to the en value when the active locale lacks the key', () => {
    setLocale('ru') // ru catalog is not authored until phase 6 → per-key en fallback
    expect(t('settings.appearance.language.label')).toBe('Language')
  })

  it('returns the key string for a missing key and warns once (dev-only)', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      expect(t('a.missing.key')).toBe('a.missing.key')
      // Second call for the same key must NOT warn again.
      expect(t('a.missing.key')).toBe('a.missing.key')
      expect(warn).toHaveBeenCalledTimes(1)
      expect(warn).toHaveBeenCalledWith('[i18n] missing translation key: a.missing.key')
    } finally {
      warn.mockRestore()
    }
  })
})

describe('formatMessage — interpolation', () => {
  it('substitutes named params', () => {
    expect(formatMessage('Hello, {name}', { name: 'Igor' }, 'en')).toBe('Hello, Igor')
  })

  it('leaves an unknown param placeholder visible', () => {
    expect(formatMessage('Hello, {name}', {}, 'en')).toBe('Hello, {name}')
  })
})

describe('Intl.PluralRules pins (environment ICU)', () => {
  it('selects the expected Russian categories for 1, 2, 5', () => {
    const ru = new Intl.PluralRules('ru')
    expect(ru.select(1)).toBe('one')
    expect(ru.select(2)).toBe('few')
    expect(ru.select(5)).toBe('many')
  })

  it('selects one/other for English', () => {
    const en = new Intl.PluralRules('en')
    expect(en.select(1)).toBe('one')
    expect(en.select(2)).toBe('other')
  })
})

describe('formatMessage — pluralization', () => {
  // Distinct Russian words per category make the picked arm unambiguous.
  const RU = '{count, plural, one {# час} few {# часа} many {# часов} other {# часов}}'

  it('picks the Russian one/few/many arm for counts 1, 2, 5', () => {
    expect(formatMessage(RU, { count: 1 }, 'ru')).toBe('1 час')
    expect(formatMessage(RU, { count: 2 }, 'ru')).toBe('2 часа')
    expect(formatMessage(RU, { count: 5 }, 'ru')).toBe('5 часов')
  })

  it('picks the English one/other arm', () => {
    const en = '{count, plural, one {# alert} other {# alerts}}'
    expect(formatMessage(en, { count: 1 }, 'en')).toBe('1 alert')
    expect(formatMessage(en, { count: 3 }, 'en')).toBe('3 alerts')
  })

  it('prefers an exact =N arm over the category arm', () => {
    const msg = '{count, plural, =0 {no alerts} one {# alert} other {# alerts}}'
    expect(formatMessage(msg, { count: 0 }, 'en')).toBe('no alerts')
    expect(formatMessage(msg, { count: 1 }, 'en')).toBe('1 alert')
  })

  it('formats the # count as en-US even in ru locale', () => {
    expect(formatMessage(RU, { count: 1000 }, 'ru')).toBe('1,000 часов')
  })

  it('interpolates named params alongside the plural # in one template', () => {
    const msg = '{name}: {count, plural, one {# item} other {# items}}'
    expect(formatMessage(msg, { name: 'Cart', count: 2 }, 'en')).toBe('Cart: 2 items')
  })
})
