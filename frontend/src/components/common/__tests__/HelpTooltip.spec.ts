import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import HelpTooltip from '@/components/common/HelpTooltip.vue'

function getTooltipElement(): HTMLDivElement {
  const tooltip = document.body.querySelector('[role="tooltip"]')
  if (!(tooltip instanceof HTMLDivElement)) {
    throw new Error('tooltip element not found')
  }
  return tooltip
}

describe('HelpTooltip', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('keeps edge tooltips inside the viewport and points the arrow at the trigger', async () => {
    const wrapper = mount(HelpTooltip, { attachTo: document.body, props: { content: '完整校验值' } })
    const trigger = wrapper.get('.group')
    const tooltip = getTooltipElement()
    let triggerLeft = window.innerWidth - 40
    vi.spyOn(trigger.element, 'getBoundingClientRect').mockImplementation(() => ({ left: triggerLeft, top: 200, width: 32 } as DOMRect))
    vi.spyOn(tooltip, 'getBoundingClientRect').mockReturnValue({ width: 320 } as DOMRect)
    await trigger.trigger('mouseenter')
    await nextTick()
    expect(parseFloat(tooltip.style.left) + 160).toBe(window.innerWidth - 12)
    const arrow = tooltip.lastElementChild as HTMLElement
    expect(parseFloat(tooltip.style.left) - 160 + parseFloat(arrow.style.left)).toBe(triggerLeft + 16)

    triggerLeft = 0
    window.dispatchEvent(new Event('resize'))
    await nextTick()
    expect(parseFloat(tooltip.style.left) - 160).toBe(12)
    wrapper.unmount()
  })

  it('keeps the existing hover interaction by default', async () => {
    const wrapper = mount(HelpTooltip, {
      attachTo: document.body,
      props: {
        content: 'hover details',
      },
    })

    const trigger = wrapper.get('.group')
    const tooltip = getTooltipElement()

    expect(tooltip.style.display).toBe('none')

    await trigger.trigger('mouseenter')
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')

    await trigger.trigger('mouseleave')
    await nextTick()
    expect(tooltip.style.display).toBe('none')

    wrapper.unmount()
  })

  it('keeps a hover tooltip open while the pointer moves between the trigger and the tooltip', async () => {
    const wrapper = mount(HelpTooltip, {
      attachTo: document.body,
      props: {
        content: 'copyable details',
      },
    })

    const trigger = wrapper.get('.group')
    const tooltip = getTooltipElement()

    await trigger.trigger('mouseenter')
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')

    await trigger.trigger('mouseleave', { relatedTarget: tooltip })
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')

    tooltip.dispatchEvent(new MouseEvent('mouseleave', { relatedTarget: trigger.element }))
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')

    tooltip.dispatchEvent(new MouseEvent('mouseleave', { relatedTarget: null }))
    await nextTick()
    expect(tooltip.style.display).toBe('none')

    wrapper.unmount()
  })

  it('supports click-to-toggle details and closes on outside click', async () => {
    const wrapper = mount(HelpTooltip, {
      attachTo: document.body,
      props: {
        content: 'click details',
        trigger: 'click',
      },
    })

    const trigger = wrapper.get('.group')
    const tooltip = getTooltipElement()

    expect(tooltip.style.display).toBe('none')

    await trigger.trigger('click')
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')
    expect(tooltip.textContent).toContain('click details')

    const closeButton = tooltip.querySelector('button[aria-label="Close"]')
    if (!(closeButton instanceof HTMLButtonElement)) {
      throw new Error('close button not found')
    }
    closeButton.click()
    await nextTick()
    expect(tooltip.style.display).toBe('none')

    await trigger.trigger('click')
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(tooltip.style.display).toBe('none')

    wrapper.unmount()
  })

  it('allows keyboard users to read hover details and dismiss them with Escape or blur', async () => {
    const wrapper = mount(HelpTooltip, {
      attachTo: document.body,
      props: { content: '评分说明' },
      slots: { trigger: '<button type="button">查看评分说明</button>' },
    })
    const trigger = wrapper.get('button')
    const tooltip = getTooltipElement()

    trigger.element.focus()
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')
    await wrapper.get('.group').trigger('mouseleave')
    expect(tooltip.style.display).not.toBe('none')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(tooltip.style.display).toBe('none')

    trigger.element.blur()
    trigger.element.focus()
    await nextTick()
    expect(tooltip.style.display).not.toBe('none')
    trigger.element.blur()
    await nextTick()
    expect(tooltip.style.display).toBe('none')
    wrapper.unmount()
  })
})
