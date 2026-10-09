import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AiCompetitorDiscovery from './AiCompetitorDiscovery'

const getPolicy = vi.fn()
const decide = vi.fn()
const startDiscovery = vi.fn()
const getDiscovery = vi.fn()

vi.mock('../../services/api', () => ({
  brandProfileApi: {
    getPolicy: (...args: unknown[]) => getPolicy(...args),
    decideDiscoveredCompetitors: (...args: unknown[]) => decide(...args),
    startCompetitorDiscovery: (...args: unknown[]) => startDiscovery(...args),
    getCompetitorDiscovery: (...args: unknown[]) => getDiscovery(...args),
  },
}))

const candidate = (id: string, term: string, confidence: 'high' | 'medium') => ({
  term,
  status: 'suggested',
  source: 'ai_discovery',
  manual_approved: false,
  source_urls: [],
  legacy: false,
  discovery_id: id,
  candidate_domain: `${id}.com`,
  candidate_url: `https://${id}.com`,
  rationale: 'Aynı ürünü sunuyor',
  confidence,
  verification_status: 'direct',
  profile_fingerprint: 'fp',
})

const policy = {
  competitor_terms: [
    candidate('high', 'Yüksek Rakip', 'high'),
    candidate('mid', 'Orta Rakip', 'medium'),
  ],
  competitor_policy: { ads: 'block', seo: 'block', social: 'block' },
  topic_policy: { excluded_terms: [], excluded_aliases: [] },
  competitor_url_decisions: {},
  policy_version: 1,
  anchor_version: 1,
  discovery_impact: {
    fresh_channel_run_count: 2,
    profile_fingerprint: 'fp',
    has_stale_suggestions: false,
  },
}

describe('AiCompetitorDiscovery', () => {
  beforeEach(() => {
    getPolicy.mockReset().mockResolvedValue({ data: policy })
    decide.mockReset().mockResolvedValue({ data: { ...policy, competitor_terms: [] } })
    startDiscovery.mockReset().mockResolvedValue({
      data: { task_id: 'discovery-task', status: 'pending', reused: false },
    })
    getDiscovery.mockReset().mockResolvedValue({
      data: {
        task_id: 'discovery-task',
        status: 'completed',
        progress: 100,
        candidates: [],
        warnings: [],
      },
    })
  })

  it('yüksek güveni seçili, orta güveni seçimsiz getirir ve etkiyi onayda gösterir', async () => {
    render(<AiCompetitorDiscovery workspaceId={21} confirmed />)
    const high = (await screen.findByLabelText('Yüksek Rakip seç')) as HTMLInputElement
    const medium = screen.getByLabelText('Orta Rakip seç') as HTMLInputElement
    expect(high.checked).toBe(true)
    expect(medium.checked).toBe(false)

    fireEvent.click(screen.getByText('1 rakibi onayla'))
    expect(screen.getByRole('dialog').textContent).toContain('2')
    expect(screen.getByRole('dialog').textContent).toContain('güncel kanal çalışması')
    fireEvent.click(screen.getByRole('button', { name: 'Onayla' }))
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1))
    expect(decide.mock.calls[0][1]).toEqual([
      { discovery_id: 'high', decision: 'approved', term: 'Yüksek Rakip' },
    ])
  })

  it('onaylanan AI rakiplerini listeler ve çıkarma rejected kararı gönderir', async () => {
    getPolicy.mockResolvedValue({
      data: {
        ...policy,
        competitor_terms: [
          ...policy.competitor_terms,
          { ...candidate('appr', 'Onaylı Rakip', 'high'), status: 'approved' },
        ],
      },
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<AiCompetitorDiscovery workspaceId={21} confirmed />)
    expect(await screen.findByText('Onaylı Rakip')).toBeTruthy()
    expect(screen.getByText('Engelleniyor')).toBeTruthy()
    fireEvent.click(screen.getByTitle('Engellemeden çıkar'))
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1))
    expect(decide.mock.calls[0][1]).toEqual([
      { discovery_id: 'appr', decision: 'rejected', term: 'Onaylı Rakip' },
    ])
    confirmSpy.mockRestore()
  })

  it('bayat öneride onayı kapatır', async () => {
    getPolicy.mockResolvedValue({
      data: {
        ...policy,
        discovery_impact: { ...policy.discovery_impact, has_stale_suggestions: true },
      },
    })
    render(<AiCompetitorDiscovery workspaceId={21} confirmed />)
    expect(await screen.findByText(/Profil değişti, önerileri yenileyin/)).toBeTruthy()
    expect(
      (screen.getByText('1 rakibi onayla').closest('button') as HTMLButtonElement).disabled
    ).toBe(true)
  })

  it('onaylı keşif geçmişinde yenileme gerçek force refresh gönderir', async () => {
    getPolicy.mockResolvedValue({
      data: {
        ...policy,
        competitor_terms: [{ ...candidate('appr', 'Onaylı Rakip', 'high'), status: 'approved' }],
      },
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<AiCompetitorDiscovery workspaceId={21} confirmed />)
    fireEvent.click(await screen.findByText('Önerileri yenile'))
    await waitFor(() => expect(startDiscovery).toHaveBeenCalledWith(21, true))
    confirmSpy.mockRestore()
  })

  it('wizard auto-start eder ve geçişte onFinish çağırır', async () => {
    const onFinish = vi.fn()
    render(
      <AiCompetitorDiscovery
        workspaceId={21}
        confirmed
        variant="wizard"
        autoStart
        onFinish={onFinish}
      />
    )
    await waitFor(() => expect(startDiscovery).toHaveBeenCalledWith(21, false))
    expect(await screen.findByText('Önerileri yenile')).toBeInTheDocument()
    fireEvent.click(await screen.findByText('Şimdilik geç'))
    expect(onFinish).toHaveBeenCalledTimes(1)
  })
})
