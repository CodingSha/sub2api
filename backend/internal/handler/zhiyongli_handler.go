package handler

import (
	"crypto/subtle"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/pkg/response"
	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
	"github.com/Wei-Shaw/sub2api/internal/zhiyongli"
	"github.com/gin-gonic/gin"
)

type ZhiyongliHandler struct{ store *zhiyongli.Store }

func NewZhiyongliHandler(db *sql.DB) *ZhiyongliHandler {
	return &ZhiyongliHandler{store: zhiyongli.NewStore(db)}
}
func zhiyongliError(c *gin.Context, e error) {
	var bad *zhiyongli.InvalidError
	switch {
	case errors.Is(e, zhiyongli.ErrNotFound):
		response.NotFound(c, e.Error())
	case errors.Is(e, zhiyongli.ErrConflict):
		response.Error(c, http.StatusConflict, e.Error())
	case errors.As(e, &bad):
		response.BadRequest(c, bad.Error())
	default:
		response.ErrorFrom(c, e)
	}
}
func zhiyongliID(c *gin.Context) (int64, bool) {
	id, e := strconv.ParseInt(c.Param("id"), 10, 64)
	if e != nil || id <= 0 {
		response.BadRequest(c, "报告ID无效")
		return 0, false
	}
	return id, true
}
func zhiyongliPage(c *gin.Context) (int, int, bool) {
	page, e := strconv.Atoi(c.DefaultQuery("page", "1"))
	size, e2 := strconv.Atoi(c.DefaultQuery("page_size", "20"))
	if e != nil || e2 != nil || page < 1 || page > 100000 || size < 1 || size > 100 {
		response.BadRequest(c, "分页参数无效")
		return 0, 0, false
	}
	return page, size, true
}
func (h *ZhiyongliHandler) list(c *gin.Context, owner int64) {
	p, n, ok := zhiyongliPage(c)
	if !ok {
		return
	}
	f := zhiyongli.Filter{UserID: owner, Page: p, PageSize: n, PeriodType: c.Query("period_type"), Status: c.Query("publication_status"), Search: c.Query("search")}
	if f.PeriodType != "" && f.PeriodType != "week" && f.PeriodType != "month" || f.Status != "" && f.Status != "draft" && f.Status != "published" && f.Status != "superseded" || len(f.Search) > 200 {
		response.BadRequest(c, "筛选参数无效")
		return
	}
	items, total, e := h.store.List(c.Request.Context(), f)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Paginated(c, items, total, p, n)
}
func (h *ZhiyongliHandler) MyReports(c *gin.Context) {
	s, ok := middleware.GetAuthSubjectFromContext(c)
	if !ok {
		response.Unauthorized(c, "请先登录")
		return
	}
	h.list(c, s.UserID)
}
func (h *ZhiyongliHandler) AdminReports(c *gin.Context) { h.list(c, 0) }
func (h *ZhiyongliHandler) get(c *gin.Context, owner int64) {
	id, ok := zhiyongliID(c)
	if !ok {
		return
	}
	r, e := h.store.Get(c.Request.Context(), id, owner)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Success(c, r)
}
func (h *ZhiyongliHandler) MyReport(c *gin.Context) {
	s, ok := middleware.GetAuthSubjectFromContext(c)
	if !ok {
		response.Unauthorized(c, "请先登录")
		return
	}
	h.get(c, s.UserID)
}
func (h *ZhiyongliHandler) AdminReport(c *gin.Context) { h.get(c, 0) }
func (h *ZhiyongliHandler) Import(c *gin.Context) {
	actor, ok := middleware.GetAuthSubjectFromContext(c)
	if !ok {
		response.Unauthorized(c, "请先登录")
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 16<<20)
	d := json.NewDecoder(c.Request.Body)
	d.DisallowUnknownFields()
	var b zhiyongli.Bundle
	if e := d.Decode(&b); e != nil {
		response.BadRequest(c, "结果包格式不正确或超过16MB")
		return
	}
	if e := d.Decode(new(any)); e != io.EOF {
		response.BadRequest(c, "结果包只能包含一个JSON对象")
		return
	}
	run, e := h.store.Import(c.Request.Context(), b, actor.UserID)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Success(c, run)
}
func (h *ZhiyongliHandler) Publish(c *gin.Context) {
	actor, ok := middleware.GetAuthSubjectFromContext(c)
	if !ok {
		response.Unauthorized(c, "请先登录")
		return
	}
	id, ok := zhiyongliID(c)
	if !ok {
		return
	}
	var input struct {
		Revision int `json:"revision"`
	}
	if e := c.ShouldBindJSON(&input); e != nil || input.Revision < 1 {
		response.BadRequest(c, "缺少预期报告修订")
		return
	}
	r, e := h.store.Publish(c.Request.Context(), id, input.Revision, actor.UserID)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Success(c, r)
}
func (h *ZhiyongliHandler) Runs(c *gin.Context) {
	p, n, ok := zhiyongliPage(c)
	if !ok {
		return
	}
	items, total, e := h.store.Runs(c.Request.Context(), p, n)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Paginated(c, items, total, p, n)
}

func (h *ZhiyongliHandler) StartJobs(c *gin.Context) {
	actor, ok := middleware.GetAuthSubjectFromContext(c)
	if !ok {
		response.Unauthorized(c, "请先登录")
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 8192)
	d := json.NewDecoder(c.Request.Body)
	d.DisallowUnknownFields()
	var input zhiyongli.StartJobs
	if e := d.Decode(&input); e != nil {
		response.BadRequest(c, "启动参数格式无效")
		return
	}
	if e := d.Decode(new(any)); e != io.EOF {
		response.BadRequest(c, "启动参数只能包含一个JSON对象")
		return
	}
	jobs, e := h.store.StartJobs(c.Request.Context(), input, actor.UserID)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Success(c, jobs)
}

func (h *ZhiyongliHandler) Jobs(c *gin.Context) {
	p, n, ok := zhiyongliPage(c)
	if !ok {
		return
	}
	jobs, total, e := h.store.Jobs(c.Request.Context(), p, n)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Paginated(c, jobs, total, p, n)
}

func (h *ZhiyongliHandler) ResumeJob(c *gin.Context) {
	id, ok := zhiyongliID(c)
	if !ok {
		return
	}
	j, e := h.store.ResumeJob(c.Request.Context(), id)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Success(c, j)
}

func zhiyongliWorkerAuthorized(c *gin.Context) bool {
	// 专用凭证仅用于已登记的调度与候选接口，不能发布或调用通用管理员功能。
	path := os.Getenv("ZHIYONGLI_WORKER_KEY_FILE")
	info, statErr := os.Lstat(path)
	if !filepath.IsAbs(path) || statErr != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 8192 {
		response.Unauthorized(c, "Worker认证失败")
		return false
	}
	key, e := os.ReadFile(path)
	if e != nil || len(strings.TrimSpace(string(key))) < 32 || subtle.ConstantTimeCompare([]byte(strings.TrimSpace(string(key))), []byte(c.GetHeader("X-Zhiyongli-Worker-Key"))) != 1 {
		response.Unauthorized(c, "Worker认证失败")
		return false
	}
	return true
}

func (h *ZhiyongliHandler) ImportJob(c *gin.Context) {
	if !zhiyongliWorkerAuthorized(c) {
		return
	}
	var e error
	id, ok := zhiyongliID(c)
	if !ok {
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 16<<20)
	d := json.NewDecoder(c.Request.Body)
	d.DisallowUnknownFields()
	var bundle zhiyongli.Bundle
	if e = d.Decode(&bundle); e != nil {
		response.BadRequest(c, "候选报告格式无效")
		return
	}
	if e = d.Decode(new(any)); e != io.EOF {
		response.BadRequest(c, "候选报告只能包含一个JSON对象")
		return
	}
	run, e := h.store.ImportJob(c.Request.Context(), id, bundle)
	if e != nil {
		zhiyongliError(c, e)
		return
	}
	response.Success(c, run)
}

func (h *ZhiyongliHandler) Schedule(c *gin.Context) {
	if !zhiyongliWorkerAuthorized(c) {
		return
	}
	if c.Request.ContentLength != 0 {
		response.BadRequest(c, "调度不接受请求参数，范围和身份由服务端配置决定")
		return
	}
	path := os.Getenv("ZHIYONGLI_SCHEDULE_CONFIG")
	if path == "" {
		response.Success(c, zhiyongli.ScheduleResult{Enabled: false})
		return
	}
	info, err := os.Lstat(path)
	if !filepath.IsAbs(path) || err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 8192 {
		response.BadRequest(c, "自动评审配置必须为私有普通文件")
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		zhiyongliError(c, err)
		return
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	var config zhiyongli.ScheduleConfig
	if decoder.Decode(&config) != nil || decoder.Decode(new(any)) != io.EOF {
		response.BadRequest(c, "自动评审配置格式无效")
		return
	}
	result, err := h.store.ScheduleDue(c.Request.Context(), config, time.Now())
	if err != nil {
		zhiyongliError(c, err)
		return
	}
	response.Success(c, result)
}
