package zhiyongli

import "testing"

func TestPersonalScopeRejectsAmbiguousConfiguration(t *testing.T) {
	for _, raw := range []string{"0", "-1", "22,22", "22,", " ", "+22", "２２", "9223372036854775808"} {
		t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", raw)
		if _, err := excludedPersonalUsers(); err == nil {
			t.Fatalf("未拒绝错误配置 %q", raw)
		}
	}
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "22, 31")
	if personalUserAllowed(22) == nil || personalUserAllowed(31) == nil || personalUserAllowed(2) != nil {
		t.Fatal("个人评分范围不正确")
	}
}
