/**
 * "Gelişmiş ayarlar" davranış testi (plan v13):
 * - provenance etiketli chip'ler; X = manuel onayı kaldır (rejected kararı API'ye gider)
 * - URL destekli terimde kaldırma sonrası yönlendirme mesajı gösterilir
 * - eski alias kuralları salt-görünür + kaldırılabilir; yeni alias eklenemez
 * - stale bandı freshness prop'undan iki nedeni ayrı gösterir
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import PolicyPanel from './PolicyPanel'

const upsert = vi.fn(() => Promise.resolve({ data: {} }))

vi.mock('../services/api', () => ({
  // Gerçek helper'ın minimal eşdeğeri (modül mock'u gerçek implementasyonu siler)
  poolFreshnessLabel: (f: {
    policy_stale?: boolean
    relevance_stale?: boolean
    strategy_stale?: boolean
  }) =>
    [
      f.policy_stale && 'Politika değişti',
      f.relevance_stale && 'Profil değişti (relevance bayat)',
      f.strategy_stale && 'Kanal stratejisi değişti',
    ]
      .filter(Boolean)
      .join(' · ') || 'Havuz güncel değil',
  brandProfileApi: {
    getPolicy: vi.fn(() =>
      Promise.resolve({
        data: {
          competitor_terms: [
            {
              term: 'fintables',
              status: 'approved',
              source: 'user',
              manual_approved: true,
              source_urls: ['fintables.com'],
              legacy: false,
            },
            {
              term: 'ekofin',
              status: 'approved',
              source: 'domain',
              manual_approved: false,
              source_urls: [],
              legacy: true,
            },
          ],
          competitor_policy: { ads: 'block', seo: 'block', social: 'block' },
          topic_policy: {
            excluded_terms: [{ term: 'temettü', status: 'approved' }],
            excluded_aliases: [{ term: 'temettu', status: 'approved' }],
          },
          competitor_url_decisions: {},
          policy_version: 3,
          anchor_version: 2,
        },
      })
    ),
    upsertPolicyTerm: (...args: unknown[]) => upsert(...(args as [])),
    setCompetitorPolicy: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

async function openPanel() {
  render(
    <PolicyPanel
      workspaceId={21}
      workspaceName="Optimice"
      freshness={{ channel_pool_stale: true, policy_stale: true, relevance_stale: false }}
    />
  )
  const toggle = await screen.findByText(/Gelişmiş ayarlar — Optimice/)
  fireEvent.click(toggle)
}

describe('PolicyPanel (Gelişmiş ayarlar)', () => {
  beforeEach(() => upsert.mockClear())

  it('provenance etiketli chipleri ve alias bölümünü gösterir', async () => {
    await openPanel()
    expect(await screen.findByText('fintables')).toBeTruthy()
    expect(screen.getByText('Rakip URL + Manuel')).toBeTruthy()
    expect(screen.getByText('eski kayıt')).toBeTruthy()
    expect(screen.getByText('Eski alias kuralları')).toBeTruthy()
    expect(screen.getByText('temettu')).toBeTruthy()
    // Topic serbest-ekleme bloğu KALKTI
    expect(screen.queryByPlaceholderText(/Konu terimi ekle/)).toBeNull()
    expect(screen.queryByText(/Rakip URL'lerinden öner/)).toBeNull()
  })

  it('stale bandını policy nedeniyle gösterir', async () => {
    await openPanel()
    expect(screen.getByTestId('policy-stale-banner').textContent).toContain('Politika değişti')
  })

  it('yalnız strateji bayatsa "Kanal stratejisi değişti" gösterir (Profil değişti DEĞİL)', async () => {
    render(
      <PolicyPanel
        workspaceId={21}
        workspaceName="Optimice"
        freshness={{
          channel_pool_stale: true,
          policy_stale: false,
          relevance_stale: false,
          strategy_stale: true,
        }}
      />
    )
    const banner = await screen.findByTestId('policy-stale-banner')
    expect(banner.textContent).toContain('Kanal stratejisi değişti')
    expect(banner.textContent).not.toContain('Profil değişti')
  })

  it('manuel kaldırma rejected kararı gönderir; URL destekli terimde yönlendirme mesajı çıkar', async () => {
    await openPanel()
    const chip = (await screen.findByText('fintables')).closest('.polx-term') as HTMLElement
    const removeBtn = chip.querySelector('button') as HTMLButtonElement
    fireEvent.click(removeBtn)
    await waitFor(() =>
      expect(upsert).toHaveBeenCalledWith(21, 'fintables', 'rejected', 'competitor')
    )
    expect(screen.getByText(/engellenmeye devam ediyor/)).toBeTruthy()
  })

  it('manuel terim ekleme approved kararı gönderir', async () => {
    await openPanel()
    const input = screen.getByPlaceholderText('Manuel rakip terimi ekle')
    fireEvent.change(input, { target: { value: 'investing' } })
    fireEvent.click(screen.getByText('Ekle'))
    await waitFor(() =>
      expect(upsert).toHaveBeenCalledWith(21, 'investing', 'approved', 'competitor')
    )
  })

  it('alias kaldırma topic_alias kind ile gider', async () => {
    await openPanel()
    const chip = (await screen.findByText('temettu')).closest('.polx-term') as HTMLElement
    const removeBtn = chip.querySelector('button') as HTMLButtonElement
    fireEvent.click(removeBtn)
    await waitFor(() =>
      expect(upsert).toHaveBeenCalledWith(21, 'temettu', 'rejected', 'topic_alias')
    )
  })
})
