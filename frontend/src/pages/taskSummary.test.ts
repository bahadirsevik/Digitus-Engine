import { describe, expect, it } from 'vitest'
import { summarizeTask } from './taskSummary'

describe('summarizeTask', () => {
  it('SEO: üretilen ile iddia incelemesi gereken içerikleri ayrı söyler', () => {
    const r = summarizeTask({
      task_type: 'seo_content',
      status: 'completed',
      scoring_run_id: 97,
      result_data: { content_ids: [229, 230], total: 2, claim_blocked_count: 2 },
    })
    expect(r.summary).toBe('2 SEO içeriği üretildi.')
    expect(r.warnings).toEqual([
      '2 içerik, iddia kontrolü nedeniyle yayın öncesi inceleme gerektiriyor.',
    ])
    expect(r.link).toEqual({ to: '/seo-geo?run_id=97', label: 'İçerikleri görüntüle' })
  })

  it('SEO: kısmi başarısızlığı ve iddia incelemesini ayrı ayrı gösterir', () => {
    const r = summarizeTask({
      task_type: 'seo_content',
      status: 'completed',
      scoring_run_id: 97,
      result_data: {
        total: 8,
        requested_count: 10,
        failed_keywords: [{ keyword: 'a' }],
        claim_blocked_count: 1,
      },
    })
    expect(r.summary).toBe('10 kelimeden 8 tanesi için SEO içeriği üretildi.')
    expect(r.warnings).toEqual([
      '2 kelime için içerik üretilemedi.',
      '1 içerik, iddia kontrolü nedeniyle yayın öncesi inceleme gerektiriyor.',
    ])
  })

  it('ADS: bağlantı görevin ürettiği seti açar', () => {
    const r = summarizeTask({
      task_type: 'ads',
      status: 'completed',
      scoring_run_id: 97,
      result_data: {
        ad_group_count: 1,
        version_number: 1,
        failed_groups: 0,
        generation_set_id: 46,
      },
    })
    expect(r.summary).toBe('1 reklam grubu üretildi (sürüm 1).')
    expect(r.warnings).toEqual([])
    expect(r.link?.to).toBe('/ads?run_id=97&set_id=46')
  })

  it('kanal ataması: iç içe delivery_summary içinden kanal sayılarını okur', () => {
    const r = summarizeTask({
      task_type: 'channel_assignment',
      status: 'completed',
      scoring_run_id: 97,
      result_data: {
        delivery_summary: {
          delivery_summary: {
            channels: {
              ADS: { kept_count: 5, unfilled_count: 0 },
              SEO: { kept_count: 3, unfilled_count: 2 },
            },
          },
        },
      },
    })
    expect(r.summary).toBe('Kanal havuzları: ADS 5 · SEO 3 kelime.')
    expect(r.warnings[0]).toContain('SEO kanalında')
    expect(r.link?.to).toBe('/keywords?view=scores&run_id=97')
  })

  it('eski kayıtta alan yoksa özet uydurmaz; tamamlanmamış görevde bağlantı vermez', () => {
    expect(
      summarizeTask({ task_type: 'seo_content', status: 'completed', result_data: {} }).summary
    ).toBeNull()
    expect(
      summarizeTask({
        task_type: 'seo_content',
        status: 'failed',
        scoring_run_id: 1,
        result_data: { total: 0 },
      })
    ).toEqual({ summary: null, warnings: [], link: null })
  })
})
