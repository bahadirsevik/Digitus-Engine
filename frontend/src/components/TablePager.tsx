import { ChevronLeft, ChevronRight } from 'lucide-react'
import './TablePager.css'

/** "51–100 / 129 kelime" + Önceki/Sonraki. page 0 tabanlıdır. */
export default function TablePager({
  page,
  pageSize,
  total,
  unit,
  onPage,
  disabled = false,
}: {
  page: number
  pageSize: number
  total: number
  unit: string
  onPage: (page: number) => void
  disabled?: boolean
}) {
  if (total <= 0) return null
  const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1)
  const from = page * pageSize + 1
  const to = Math.min(total, (page + 1) * pageSize)
  return (
    <div className="table-pager">
      <span className="table-pager-range">
        {from}–{to} / {total} {unit}
      </span>
      {lastPage > 0 && (
        <div className="table-pager-buttons">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => onPage(page - 1)}
            disabled={disabled || page <= 0}
          >
            <ChevronLeft size={14} />
            Önceki
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => onPage(page + 1)}
            disabled={disabled || page >= lastPage}
          >
            Sonraki
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  )
}
