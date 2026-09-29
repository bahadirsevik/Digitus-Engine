/**
 * Keyword + Marka Odakları onay adımı — Claude Design "Marka Profili"
 * tasarımına göre chip tabanlı görünüm. İşleyiş değişmedi:
 * keyword ekle/sil (min 1 / max 20), odak grupları + "Kaynağı düzenle",
 * profil değişince stale banner + "yeniden üret", onay → confirmed.
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, Loader2, Plus, X, RefreshCw, Pencil } from 'lucide-react'
import { workspaceApi, WorkspaceResponse, AnchorGroup } from '../../services/api'
import {
  WorkspaceListRow,
  listToTextarea,
  splitNewlineItems,
  extractErrorMessage,
} from '../brandProfileState'

const EDITABLE_GROUP_FIELDS: Record<string, { label: string; isList: boolean }> = {
  products: { label: 'Ürünler', isList: true },
  // Faz D: services ve protected_themes artık anchor üretiyor; eşlemede
  // yoksa grup görünür ama "Kaynağı düzenle" butonu çıkmazdı.
  services: { label: 'Hizmetler', isList: true },
  protected_themes: { label: 'Korunacak Temalar', isList: true },
  use_cases: { label: 'Kullanım Alanları', isList: true },
  problems_solved: { label: 'Çözülen Problemler', isList: true },
  brand_terms: { label: 'Marka Terimleri', isList: true },
  sector_audience: { label: 'Hedef Kitle', isList: false },
}

const MAX_KEYWORDS = 20

export default function KeywordAnchorReview({
  workspace,
  onWorkspaceUpdated,
  onApproved,
}: {
  workspace: WorkspaceListRow
  onWorkspaceUpdated: (workspace: WorkspaceResponse) => void
  onApproved: (workspace: WorkspaceResponse) => void
}) {
  const [keywords, setKeywords] = useState<string[]>(
    (workspace.suggested_keywords || []).filter(Boolean)
  )
  const [addingKeyword, setAddingKeyword] = useState(false)
  const [newKeyword, setNewKeyword] = useState('')
  const [groups, setGroups] = useState<AnchorGroup[]>([])
  const [groupsLoading, setGroupsLoading] = useState(false)
  const [editingField, setEditingField] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [profileDirty, setProfileDirty] = useState(false)
  const [savingEdit, setSavingEdit] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setKeywords((workspace.suggested_keywords || []).filter(Boolean))
  }, [workspace.id, workspace.suggested_keywords])

  const fetchGroups = useCallback(async () => {
    setGroupsLoading(true)
    try {
      const res = await workspaceApi.anchorsPreview(workspace.id)
      setGroups(res.data.groups || [])
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setGroupsLoading(false)
    }
  }, [workspace.id])

  useEffect(() => {
    fetchGroups()
  }, [fetchGroups, workspace.profile_data])

  const removeKeyword = (index: number) => {
    setKeywords((items) => items.filter((_, i) => i !== index))
  }

  const commitNewKeyword = () => {
    const text = newKeyword.trim()
    if (text && keywords.length < MAX_KEYWORDS) {
      setKeywords((items) => [...items, text])
    }
    setNewKeyword('')
    setAddingKeyword(false)
  }

  const startEdit = (field: string) => {
    const profile = (workspace.profile_data || {}) as Record<string, unknown>
    const config = EDITABLE_GROUP_FIELDS[field]
    if (!config) return
    setEditingField(field)
    setEditText(
      config.isList ? listToTextarea(profile[field]) : String(profile.target_audience || '')
    )
  }

  const saveEdit = async () => {
    if (!editingField) return
    const config = EDITABLE_GROUP_FIELDS[editingField]
    setSavingEdit(true)
    setError('')
    try {
      const profilePatch: Record<string, unknown> = config.isList
        ? { [editingField]: splitNewlineItems(editText) }
        : { target_audience: editText.trim() }
      const res = await workspaceApi.approveProfile(workspace.id, {
        profile_data: profilePatch,
        rerun_keywords: false,
      })
      onWorkspaceUpdated(res.data)
      setEditingField(null)
      setProfileDirty(true)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setSavingEdit(false)
    }
  }

  const handleRegenerateKeywords = async () => {
    setRegenerating(true)
    setError('')
    try {
      const res = await workspaceApi.approveProfile(workspace.id, { rerun_keywords: true })
      setProfileDirty(false)
      onWorkspaceUpdated(res.data)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setRegenerating(false)
    }
  }

  const handleApprove = async () => {
    setApproving(true)
    setError('')
    try {
      const cleaned = keywords.map((kw) => kw.trim()).filter(Boolean)
      const res = await workspaceApi.approveKeywords(workspace.id, { keywords: cleaned })
      onApproved(res.data)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setApproving(false)
    }
  }

  const busy = savingEdit || regenerating || approving

  return (
    <div className="bpx-fade">
      <h3 className="bpx-h3">Keyword Önerileri ve Marka Odakları</h3>
      <p className="bpx-sub">Detaylı hacim ve rekabet analizi bir sonraki adımda yapılacak.</p>

      {profileDirty && (
        <div className="bpx-stale">
          <span>Marka odakları güncellendi; keyword önerileri eski profile göre üretilmişti.</span>
          <button
            type="button"
            className="bpx-btn-raised"
            onClick={handleRegenerateKeywords}
            disabled={busy}
          >
            {regenerating ? <Loader2 size={13} className="spin-icon" /> : <RefreshCw size={13} />}
            Keyword önerilerini yeniden üret
          </button>
        </div>
      )}

      <div className="bpx-kw-header">
        <h4 className="bpx-h4">Önerilen Keywordler ({keywords.length})</h4>
        <button
          type="button"
          className="bpx-btn-raised"
          onClick={() => setAddingKeyword(true)}
          disabled={busy || keywords.length >= MAX_KEYWORDS || addingKeyword}
        >
          <Plus size={14} /> Ekle
        </button>
      </div>
      <div className="bpx-chiprow">
        {keywords.map((kw, idx) => (
          <span key={`${kw}-${idx}`} className="bpx-chip has-x">
            {kw}
            <button
              type="button"
              className="bpx-chip-x"
              aria-label={`${kw} sil`}
              title="Sil"
              onClick={() => removeKeyword(idx)}
              disabled={busy || keywords.length <= 1}
            >
              <X size={12} strokeWidth={2} />
            </button>
          </span>
        ))}
        {addingKeyword && (
          <span className="bpx-chip-add">
            <input
              autoFocus
              value={newKeyword}
              placeholder="Yeni keyword…"
              onChange={(e) => setNewKeyword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitNewKeyword()
                if (e.key === 'Escape') {
                  setNewKeyword('')
                  setAddingKeyword(false)
                }
              }}
              onBlur={commitNewKeyword}
            />
          </span>
        )}
      </div>

      <h4 className="bpx-h4 bpx-focus-title">Marka Odakları</h4>
      <p className="bpx-focus-sub">
        Analizde referans alınacak marka konuları. Profil kartlarından otomatik üretilir;
        değiştirmek için ilgili kaynağı düzenleyin.
        {groupsLoading && <Loader2 size={13} className="spin-icon" />}
      </p>
      <div className="bpx-focus-list">
        {groups.map((group) => (
          <div className="bpx-focus-group" key={group.source_field}>
            <div className="bpx-focus-group-head">
              <span className="bpx-focus-group-title">{group.label}</span>
              {EDITABLE_GROUP_FIELDS[group.source_field] && (
                <button
                  type="button"
                  className="bpx-edit-btn"
                  onClick={() => startEdit(group.source_field)}
                  disabled={busy}
                >
                  <Pencil size={13} /> Kaynağı düzenle
                </button>
              )}
            </div>
            <div className="bpx-chiprow is-group">
              {group.kept_items.map((item, idx) => (
                <span key={idx} className="bpx-chip">
                  {item}
                </span>
              ))}
            </div>
            {group.excluded_items.length > 0 && (
              <p className="bpx-focus-excluded">
                Dışlanan temalarla çakıştığı için hariç: {group.excluded_items.join(', ')}
              </p>
            )}
            {editingField === group.source_field && (
              <div className="bpx-focus-edit">
                {EDITABLE_GROUP_FIELDS[group.source_field].isList ? (
                  <textarea
                    className="bpx-textarea"
                    rows={4}
                    placeholder="Her satıra bir kalem"
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    disabled={savingEdit}
                  />
                ) : (
                  <input
                    className="bpx-input"
                    type="text"
                    placeholder="Hedef kitle"
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    disabled={savingEdit}
                  />
                )}
                <div className="bpx-focus-edit-actions">
                  <button
                    type="button"
                    className="bpx-btn-ghost"
                    onClick={() => setEditingField(null)}
                    disabled={savingEdit}
                  >
                    Vazgeç
                  </button>
                  <button
                    type="button"
                    className="bpx-btn-primary"
                    onClick={saveEdit}
                    disabled={savingEdit}
                  >
                    {savingEdit ? <Loader2 size={13} className="spin-icon" /> : null}
                    Kaydet
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        {!groupsLoading && groups.length === 0 && (
          <p className="bpx-focus-sub">Marka odağı üretilemedi. Profil kartlarını kontrol edin.</p>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="bpx-modal-footer is-end bpx-inline-footer">
        <button
          type="button"
          className="bpx-btn-primary"
          onClick={handleApprove}
          disabled={busy || keywords.every((kw) => !kw.trim())}
        >
          {approving ? (
            <Loader2 size={16} className="spin-icon" />
          ) : (
            <Check size={16} strokeWidth={2.4} />
          )}
          Onayla ve analize geç
        </button>
      </div>
    </div>
  )
}
