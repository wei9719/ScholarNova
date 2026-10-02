import { useState, useRef, type FormEvent } from 'react'
import { Search, Loader2 } from 'lucide-react'
import clsx from 'clsx'
import { useLocaleStore } from '@/stores/localeStore'
import './SearchBar.css'

interface SearchBarProps {
  defaultValue?: string
  placeholder?: string
  loading?: boolean
  size?: 'sm' | 'lg'
  onSubmit: (query: string) => void
}

export default function SearchBar({
  defaultValue = '',
  placeholder,
  loading = false,
  size = 'sm',
  onSubmit,
}: SearchBarProps) {
  const { t } = useLocaleStore()
  const [value, setValue] = useState(defaultValue)
  const inputRef = useRef<HTMLInputElement>(null)

  const defaultPlaceholder = t('search.placeholder')
  const buttonText = t('common.search')

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = value.trim()
    if (trimmed && !loading) {
      onSubmit(trimmed)
    }
  }

  return (
    <form role="search" onSubmit={handleSubmit} className={clsx('search-bar-wrapper', size === 'lg' && 'search-bar-lg')}>
      <div className="search-bar-container">
        <Search className="search-bar-icon" />
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder || defaultPlaceholder}
          aria-label={placeholder || defaultPlaceholder}
          className="search-bar-input"
          disabled={loading}
        />
        {loading && (
          <Loader2 className="search-bar-spinner" />
        )}
        <button
          type="submit"
          disabled={loading || !value.trim()}
          className={clsx(
            'search-bar-button',
            loading || !value.trim()
              ? 'search-bar-button-disabled'
              : 'search-bar-button-active'
          )}
        >
          {loading ? t('search.searching') : buttonText}
        </button>
      </div>
    </form>
  )
}
