package handler

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestZhiyongliWorkerCredentialIsRequiredAndBounded(t *testing.T) {
	gin.SetMode(gin.TestMode)
	root := t.TempDir()
	key := filepath.Join(root, "worker.key")
	token := strings.Repeat("test-only-", 8)
	if e := os.WriteFile(key, []byte(token), 0600); e != nil {
		t.Fatal(e)
	}
	h := &ZhiyongliHandler{}
	for _, tc := range []struct {
		name, path, header string
		want               int
	}{
		{"missing config", "", token, http.StatusUnauthorized},
		{"missing header", key, "", http.StatusUnauthorized},
		{"wrong key", key, "wrong", http.StatusUnauthorized},
		{"valid credential invalid body", key, token, http.StatusBadRequest},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("ZHIYONGLI_WORKER_KEY_FILE", tc.path)
			r := gin.New()
			r.POST("/jobs/:id/import", h.ImportJob)
			req := httptest.NewRequest(http.MethodPost, "/jobs/1/import", strings.NewReader("not-json"))
			req.Header.Set("X-Zhiyongli-Worker-Key", tc.header)
			out := httptest.NewRecorder()
			r.ServeHTTP(out, req)
			if out.Code != tc.want {
				t.Fatalf("got %d want %d", out.Code, tc.want)
			}
		})
	}
	for _, mode := range []os.FileMode{0644, 0666} {
		if e := os.Chmod(key, mode); e != nil {
			t.Fatal(e)
		}
		t.Setenv("ZHIYONGLI_WORKER_KEY_FILE", key)
		r := gin.New()
		r.POST("/jobs/:id/import", h.ImportJob)
		req := httptest.NewRequest(http.MethodPost, "/jobs/1/import", strings.NewReader("{}"))
		req.Header.Set("X-Zhiyongli-Worker-Key", token)
		out := httptest.NewRecorder()
		r.ServeHTTP(out, req)
		if out.Code != http.StatusUnauthorized {
			t.Fatal("公开可读密钥不能启用Worker权限")
		}
	}
}

func TestZhiyongliSchedulerDoesNotAcceptCallerSuppliedScope(t *testing.T) {
	gin.SetMode(gin.TestMode)
	root := t.TempDir()
	path := filepath.Join(root, "worker.key")
	token := strings.Repeat("test-only-", 8)
	if err := os.WriteFile(path, []byte(token), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("ZHIYONGLI_WORKER_KEY_FILE", path)
	t.Setenv("ZHIYONGLI_SCHEDULE_CONFIG", "")
	h := &ZhiyongliHandler{}
	for _, tc := range []struct {
		body, token string
		want        int
	}{
		{"", "", http.StatusUnauthorized}, {"{}", token, http.StatusBadRequest}, {"", token, http.StatusOK},
	} {
		r := gin.New()
		r.POST("/schedule", h.Schedule)
		req := httptest.NewRequest(http.MethodPost, "/schedule", strings.NewReader(tc.body))
		req.Header.Set("X-Zhiyongli-Worker-Key", tc.token)
		out := httptest.NewRecorder()
		r.ServeHTTP(out, req)
		if out.Code != tc.want {
			t.Fatalf("%d want %d", out.Code, tc.want)
		}
	}
}
