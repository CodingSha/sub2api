package zhiyongli

import "math/big"

const SampleInitialVersion = "sample-initial-20261115.1"

// SampleInitial 将未经另次复查的抽样初评与正式周期分分开保存。
// 参考分只按有依据维度的原权重归一，缺失维度保留，不补零。
type SampleInitial struct {
	Version           string   `json:"version"`
	SampleDigest      string   `json:"sample_digest"`
	AuditStatus       string   `json:"audit_status"`
	Method            string   `json:"method"`
	Exact             *string  `json:"exact"`
	Score             *string  `json:"score"`
	CoveredWeight     int64    `json:"covered_weight"`
	MissingDimensions []string `json:"missing_dimensions"`
}

func (r *Result) validateSampleInitial() error {
	s := r.SampleInitial
	if s == nil {
		return nil
	}
	if r.PeriodType != "week" || s.Version != SampleInitialVersion || !hashValid(s.SampleDigest) || s.AuditStatus != "not_reviewed" {
		return invalid("抽样初评版本、来源签名或复查状态无效")
	}
	s.Method, s.Exact, s.Score = "observed_dimension_weights", nil, nil
	s.CoveredWeight, s.MissingDimensions = 0, []string{}
	total := new(big.Rat)
	for _, dimension := range r.Dimensions {
		if dimension.Exact == nil {
			s.MissingDimensions = append(s.MissingDimensions, dimension.Code)
			continue
		}
		value, err := rational(*dimension.Exact)
		if err != nil {
			return err
		}
		weight := weights[dimension.Code]
		total.Add(total, new(big.Rat).Mul(value, big.NewRat(weight, 1)))
		s.CoveredWeight += weight
	}
	if s.CoveredWeight > 0 {
		total.Quo(total, big.NewRat(s.CoveredWeight, 1))
		s.Exact, s.Score = exact(total), display(total)
	}
	return nil
}
