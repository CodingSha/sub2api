// 独立验证冻结结果包，只读文件，不连接数据库或模型服务。
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/Wei-Shaw/sub2api/internal/zhiyongli"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "请提供需要验证的结果包文件")
		os.Exit(2)
	}
	for _, path := range os.Args[1:] {
		if err := check(path); err != nil {
			fmt.Fprintln(os.Stderr, path, err)
			os.Exit(1)
		}
	}
	fmt.Printf("已通过正式评分合同验证：%d份结果包\n", len(os.Args)-1)
}

func check(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	decoder := json.NewDecoder(io.LimitReader(f, 16<<20))
	decoder.DisallowUnknownFields()
	var bundle zhiyongli.Bundle
	if err := decoder.Decode(&bundle); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("结果包须为单个JSON对象")
	}
	if bundle.SchemaVersion != 1 || len(bundle.Reports) != 1 {
		return fmt.Errorf("仅接收单人单周期v1结果包")
	}
	return bundle.Reports[0].Validate()
}
