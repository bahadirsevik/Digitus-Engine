import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import DashboardCockpit from './DashboardCockpit'
import DashboardEmpty from './DashboardEmpty'
import {
  ChannelGroup,
  ChannelProgress,
  DashboardNextAction,
  DashboardSummary,
} from '../services/api'

function ch(over: Partial<ChannelProgress> = {}): ChannelProgress {
  return {
    pool_count: 0,
    expected_count: 0,
    generated_count: 0,
    status: 'empty',
    ...over,
  }
}

function group(over: Partial<ChannelGroup> = {}): ChannelGroup {
  return {
    ADS: ch(),
    SEO: ch(),
    SOCIAL: ch(),
    ...over,
  }
}

function makeSummary(over: Partial<DashboardSummary> = {}): DashboardSummary {
  const base: DashboardSummary = {
    workspace: {
      id: 1,
      name: 'Test WS',
      company_url: 'https://example.com',
      status: 'confirmed',
      profile_ready: true,
    },
    active_run: {
      id: 42,
      run_name: 'Run 42',
      status: 'scored',
      keyword_selection_mode: 'all',
      ads_capacity: 10,
      seo_capacity: 5,
      social_capacity: 3,
      enable_ads: true,
      enable_seo: true,
      enable_social: false,
      skip_relevance: false,
      created_at: null,
    },
    runs: [
      {
        id: 42,
        run_name: 'Run 42',
        status: 'scored',
        keyword_selection_mode: 'all',
        ads_capacity: 10,
        seo_capacity: 5,
        social_capacity: 3,
        enable_ads: true,
        enable_seo: true,
        enable_social: false,
        skip_relevance: false,
        created_at: null,
      },
    ],
    keyword_count: 25,
    pipeline: [
      {
        key: 'brand_profile',
        label: 'Marka Profili',
        state: 'complete',
        detail: 'confirmed',
        path: '/brand-profile',
      },
      {
        key: 'scoring',
        label: 'Skorlama',
        state: 'in_progress',
        detail: 'scored',
        path: '/scoring?run_id=42',
      },
    ],
    channels: group(),
    latest_export: null,
    blocking_task: null,
    active_tasks: [],
    health: { api: 'ok', google_ads: 'ok' },
    next_action: {
      key: 'compute_relevance',
      label: 'İlgi Skoru Hesapla',
      path: '/relevance?run_id=42',
      severity: 'primary',
      reason: 'Skorlama bitti.',
    },
  }
  return { ...base, ...over }
}

describe('DashboardEmpty', () => {
  it('renders the primary CTA from the summary', () => {
    const action: DashboardNextAction = {
      key: 'create_workspace',
      label: 'Marka Çalışması Oluştur',
      path: '/brand-profile',
      severity: 'primary',
      reason: 'Önce bir marka.',
    }
    render(
      <MemoryRouter>
        <DashboardEmpty nextAction={action} />
      </MemoryRouter>
    )
    expect(screen.getByText('Marka Çalışması Oluştur')).toBeInTheDocument()
    expect(screen.getByText('Önce bir marka.')).toBeInTheDocument()
  })

  it('falls back to a default CTA when no next action is supplied', () => {
    render(
      <MemoryRouter>
        <DashboardEmpty nextAction={null} />
      </MemoryRouter>
    )
    expect(screen.getByText('Marka Çalışması Oluştur')).toBeInTheDocument()
  })
})

describe('DashboardCockpit', () => {
  it('renders main action with severity class', () => {
    const summary = makeSummary({
      next_action: {
        key: 'check_failed_run',
        label: 'Skorlamayı Kontrol Et',
        path: '/scoring?run_id=42',
        severity: 'danger',
        reason: 'Aktif run hata ile sonuçlandı.',
      },
    })
    const { container } = render(
      <MemoryRouter>
        <DashboardCockpit
          summary={summary}
          loading={false}
          onChangeRun={() => {}}
          onRefresh={() => {}}
        />
      </MemoryRouter>
    )
    expect(container.querySelector('.cockpit-main-action.cta-danger')).toBeTruthy()
    expect(screen.getByText('Skorlamayı Kontrol Et')).toBeInTheDocument()
  })

  it('shows pipeline steps as links carrying run_id', () => {
    const summary = makeSummary()
    render(
      <MemoryRouter>
        <DashboardCockpit
          summary={summary}
          loading={false}
          onChangeRun={() => {}}
          onRefresh={() => {}}
        />
      </MemoryRouter>
    )
    const scoringLink = screen.getByText('Skorlama').closest('a')
    expect(scoringLink?.getAttribute('href')).toContain('run_id=42')
  })

  it('shows blocking task chip when a blocking task is active', () => {
    const summary = makeSummary({
      blocking_task: {
        task_id: 't1',
        task_type: 'scoring',
        status: 'running',
        progress: 40,
        error_message: null,
        is_blocking: true,
      },
    })
    render(
      <MemoryRouter>
        <DashboardCockpit
          summary={summary}
          loading={false}
          onChangeRun={() => {}}
          onRefresh={() => {}}
        />
      </MemoryRouter>
    )
    expect(screen.getAllByText(/Kritik işlem/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/%40/).length).toBeGreaterThan(0)
  })

  it('renders run selector and dispatches onChangeRun on change', () => {
    const onChange = vi.fn()
    const summary = makeSummary({
      runs: [
        ...makeSummary().runs,
        {
          id: 43,
          run_name: 'Run 43',
          status: 'failed',
          keyword_selection_mode: 'all',
          ads_capacity: 10,
          seo_capacity: 5,
          social_capacity: 3,
          enable_ads: true,
          enable_seo: true,
          enable_social: false,
          skip_relevance: false,
          created_at: null,
        },
      ],
    })
    render(
      <MemoryRouter>
        <DashboardCockpit
          summary={summary}
          loading={false}
          onChangeRun={onChange}
          onRefresh={() => {}}
        />
      </MemoryRouter>
    )
    const select = screen.getByRole('combobox') as HTMLSelectElement
    select.value = '43'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    expect(onChange).toHaveBeenCalledWith(43)
  })

  it('renders without crashing when optional fields are null', () => {
    const summary = makeSummary({
      active_run: null,
      runs: [],
      latest_export: null,
      blocking_task: null,
    })
    render(
      <MemoryRouter>
        <DashboardCockpit
          summary={summary}
          loading={false}
          onChangeRun={() => {}}
          onRefresh={() => {}}
        />
      </MemoryRouter>
    )
    expect(screen.getAllByText('Henüz run yok.').length).toBeGreaterThan(0)
    expect(screen.getByText('Henüz dışa aktarım yok.')).toBeInTheDocument()
  })
})
