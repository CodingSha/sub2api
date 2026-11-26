import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReviewJobs from './ReviewJobs.vue'
import Pagination from '@/components/common/Pagination.vue'
import { createI18n } from 'vue-i18n'

const api=vi.hoisted(()=>({listJobs:vi.fn(),startJobs:vi.fn(),startLastWeekJobs:vi.fn(),resumeJob:vi.fn(),listUsers:vi.fn(),showError:vi.fn(),showSuccess:vi.fn()}))
vi.mock('./api',()=>api)
vi.mock('@/api/admin/users',()=>({list:api.listUsers}))
vi.mock('@/stores/app',()=>({useAppStore:()=>({showError:api.showError,showSuccess:api.showSuccess})}))
const empty={items:[],total:0,page:1,page_size:20,pages:0}
const mountView=(open=false)=>mount(ReviewJobs,{props:{startOpen:open},global:{plugins:[createI18n({legacy:false,locale:'zh',messages:{zh:{}}})],stubs:{
  BaseDialog:{props:['show'],template:'<div v-if="show"><slot/><slot name="footer"/></div>'},
  Select:{props:['modelValue','options'],template:'<select :value="modelValue" @change="$emit(\'update:modelValue\',$event.target.value);$emit(\'change\')"><option v-for="option in options" :value="option.value">{{ option.label }}</option></select>'}
}}})
beforeEach(()=>{vi.useFakeTimers();vi.clearAllMocks();api.listJobs.mockResolvedValue(empty);api.listUsers.mockResolvedValue({...empty,items:[{id:63,email:'person@example.test'}],total:1})})
afterEach(()=>{vi.useRealTimers()})
describe('管理员评审入口',()=>{
  it('当前与历史分开查询，历史失败保留状态且不提供恢复操作',async()=>{
    api.listJobs.mockResolvedValue({...empty,items:[{id:1,latest_job_id:2,user_email:'history@example.test',state:'failed',phase:'execution_contract_failed',message:'原失败回执',period_start:'2026-11-16',period_type:'week',rules_version:'1.6',total_units:0,updated_at:'2026-11-22T00:00:00Z'}],total:1})
    const wrapper=mountView();await flushPromises()
    expect(api.listJobs).toHaveBeenCalledWith(1,{view:'current',state:'',search:'',sort:''})
    await wrapper.get('select[aria-label="评审记录范围"]').setValue('history');await flushPromises()
    expect(api.listJobs).toHaveBeenLastCalledWith(1,{view:'history',state:'',search:'',sort:''})
    expect(wrapper.text()).toContain('历史失败')
    expect(wrapper.text()).toContain('已由评审 #2 替代')
    expect(wrapper.text()).toContain('原失败回执')
    expect(wrapper.text()).not.toContain('恢复校验')
    wrapper.unmount()
  })
  it('顶部可直接跳页，排序变更从第一页对完整结果重新查询',async()=>{
    api.listJobs.mockResolvedValue({...empty,total:65,pages:4})
    const wrapper=mountView();await flushPromises()
    const pages=wrapper.findAllComponents(Pagination)
    expect(pages).toHaveLength(1)
    pages[0].vm.$emit('update:page',3);await flushPromises()
    expect(api.listJobs).toHaveBeenLastCalledWith(3,{state:'',search:'',sort:'',view:'current'})
    await wrapper.get('select[aria-label="评审排序"]').setValue('period_asc');await flushPromises()
    expect(api.listJobs).toHaveBeenLastCalledWith(1,{state:'',search:'',sort:'period_asc',view:'current'})
    expect(pages[0].props('page')).toBe(1)
    wrapper.unmount()
  })
  it('周评分一键登记全员上周，不选人不选日期；失败如实显示',async()=>{
    api.startLastWeekJobs.mockRejectedValueOnce(new Error('工作日期间来源尚未封口'))
    const wrapper=mountView(true);await flushPromises()
    expect(wrapper.find('input[type="date"]').exists()).toBe(false)
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(api.listUsers).not.toHaveBeenCalled()
    await wrapper.get('#start-review').trigger('submit');await flushPromises()
    expect(wrapper.text()).toContain('工作日期间来源尚未封口')
    expect(wrapper.emitted('close')).toBeUndefined()
    api.startLastWeekJobs.mockResolvedValueOnce([{id:1,period_start:'2026-11-02'},{id:2,period_start:'2026-11-02'}])
    await wrapper.get('#start-review').trigger('submit');await flushPromises()
    expect(api.startLastWeekJobs).toHaveBeenCalledTimes(2)
    expect(api.startJobs).not.toHaveBeenCalled()
    expect(api.showSuccess).toHaveBeenCalledWith('已登记2个账户的周评审（2026-11-02起）')
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
  it('月评分按实际账户ID与月份登记，失败保留输入，不伪造成功',async()=>{
    api.startJobs.mockRejectedValue(new Error('月评分必须从每月1日开始'))
    const wrapper=mountView(true);await flushPromises()
    await wrapper.get('select[aria-label="评审周期类型"]').setValue('month');await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('input[type="date"]').setValue('2026-09-08')
    await wrapper.get('#start-review').trigger('submit');await flushPromises()
    expect(api.startJobs).toHaveBeenCalledWith({user_ids:[63],period_type:'month',period_start:'2026-09-08'})
    expect(api.startLastWeekJobs).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('月评分必须从每月1日开始')
    expect((wrapper.get('input[type="date"]').element as HTMLInputElement).value).toBe('2026-09-08')
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })
  it('请求结果不明不给恢复入口；候选生成才允许查看报告',async()=>{
    api.listJobs.mockResolvedValue({...empty,items:[
      {id:1,user_email:'a@example.test',state:'unknown',phase:'paused',message:'结果不明',period_start:'2026-09-07',period_type:'week',rules_version:'1.6',total_units:0,completed_units:0,updated_at:'2026-11-09T00:00:00Z'},
      {id:2,user_email:'b@example.test',state:'completed',phase:'done',message:'候选已生成',period_start:'2026-09-07',period_type:'day',rules_version:'1.6',total_units:1,completed_units:1,updated_at:'2026-11-09T00:00:00Z',recheck_report_id:7,result_report_id:8}],total:2})
    const wrapper=mountView();await flushPromises()
    expect(wrapper.text()).toContain('请求结果不明')
    expect(wrapper.text()).not.toContain('恢复校验')
    expect(wrapper.text()).toContain('2026-09-07 · 日 · 1.6')
    const button=wrapper.findAll('button').find(b=>b.text()==='查看复评结果')!
    await button.trigger('click')
    expect(wrapper.emitted('reports')).toEqual([[expect.objectContaining({user_email:'b@example.test',result_report_id:8})]])
    wrapper.unmount();const count=api.listJobs.mock.calls.length;await vi.advanceTimersByTimeAsync(10000)
    expect(api.listJobs).toHaveBeenCalledTimes(count)
  })
})
