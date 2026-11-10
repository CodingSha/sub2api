import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReviewJobs from './ReviewJobs.vue'
import { createI18n } from 'vue-i18n'

const api=vi.hoisted(()=>({listJobs:vi.fn(),startJobs:vi.fn(),resumeJob:vi.fn(),listUsers:vi.fn(),showError:vi.fn()}))
vi.mock('./api',()=>api)
vi.mock('@/api/admin/users',()=>({list:api.listUsers}))
vi.mock('@/stores/app',()=>({useAppStore:()=>({showError:api.showError})}))
const empty={items:[],total:0,page:1,page_size:20,pages:0}
const mountView=(open=false)=>mount(ReviewJobs,{props:{startOpen:open},global:{plugins:[createI18n({legacy:false,locale:'zh',messages:{zh:{}}})],stubs:{
  BaseDialog:{props:['show'],template:'<div v-if="show"><slot/><slot name="footer"/></div>'},
  Select:{props:['modelValue','options'],template:'<select :value="modelValue" @change="$emit(\'update:modelValue\',$event.target.value)"><option value="week">周评分</option><option value="month">月评分</option></select>'}
}}})
beforeEach(()=>{vi.useFakeTimers();vi.clearAllMocks();api.listJobs.mockResolvedValue(empty);api.listUsers.mockResolvedValue({...empty,items:[{id:63,email:'person@example.test'}],total:1})})
afterEach(()=>{vi.useRealTimers()})
describe('管理员评审入口',()=>{
  it('按实际账户ID与周期登记，失败保留输入，不伪造成功',async()=>{
    api.startJobs.mockRejectedValue(new Error('周评分必须从周一开始'))
    const wrapper=mountView(true);await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('input[type="date"]').setValue('2026-09-08')
    await wrapper.get('form').trigger('submit');await flushPromises()
    expect(api.startJobs).toHaveBeenCalledWith({user_ids:[63],period_type:'week',period_start:'2026-09-08'})
    expect(wrapper.text()).toContain('周评分必须从周一开始')
    expect((wrapper.get('input[type="date"]').element as HTMLInputElement).value).toBe('2026-09-08')
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })
  it('请求结果不明不给恢复入口；候选生成才允许查看报告',async()=>{
    api.listJobs.mockResolvedValue({...empty,items:[
      {id:1,user_email:'a@example.test',state:'unknown',phase:'paused',message:'结果不明',period_start:'2026-09-07',period_type:'week',rules_version:'1.6',total_units:0,completed_units:0,updated_at:'2026-11-09T00:00:00Z'},
      {id:2,user_email:'b@example.test',state:'completed',phase:'done',message:'候选已生成',period_start:'2026-09-07',period_type:'week',rules_version:'1.6',total_units:1,completed_units:1,updated_at:'2026-11-09T00:00:00Z'}],total:2})
    const wrapper=mountView();await flushPromises()
    expect(wrapper.text()).toContain('请求结果不明')
    expect(wrapper.text()).not.toContain('恢复校验')
    const button=wrapper.findAll('button').find(b=>b.text()==='查看结果')!
    await button.trigger('click')
    expect(wrapper.emitted('reports')).toEqual([['b@example.test']])
    wrapper.unmount();const count=api.listJobs.mock.calls.length;await vi.advanceTimersByTimeAsync(10000)
    expect(api.listJobs).toHaveBeenCalledTimes(count)
  })
})
