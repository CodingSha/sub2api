package zhiyongli

import (
	"os"
	"strconv"
	"strings"
)

// excludedPersonalUsers 仅限制个人评审，不改变审计采集白名单或删除历史数据。
func excludedPersonalUsers() ([]int64, error) {
	raw := os.Getenv("ZHIYONGLI_EXCLUDED_USER_IDS")
	ids := []int64{}
	if raw == "" {
		return ids, nil
	}
	seen := map[int64]bool{}
	for _, part := range strings.Split(raw, ",") {
		part = strings.TrimSpace(part)
		for _, c := range part {
			if c < '0' || c > '9' {
				return nil, invalid("个人评分排除配置须为逗号分隔的正整数账户ID")
			}
		}
		id, err := strconv.ParseInt(part, 10, 64)
		if err != nil || id <= 0 || seen[id] {
			return nil, invalid("个人评分排除配置有无效或重复账户ID")
		}
		seen[id] = true
		ids = append(ids, id)
	}
	return ids, nil
}

func personalUserAllowed(id int64) error {
	ids, err := excludedPersonalUsers()
	if err != nil {
		return err
	}
	for _, excluded := range ids {
		if id == excluded {
			return invalid("该账户已排除个人评分；原始记录与历史结果保留")
		}
	}
	return nil
}
