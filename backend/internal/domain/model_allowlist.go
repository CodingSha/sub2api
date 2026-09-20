package domain

// GroupModelAllowlist 是分组级模型白名单：开启后既过滤模型列表类接口的返回内容，
// 也约束分组实际可调用的模型（网关准入在合成路由改写与调度之前完成）。
type GroupModelAllowlist struct {
	Enabled bool     `json:"enabled"`
	Models  []string `json:"models,omitempty"`
	// MultimodalModels 标记白名单中支持多模态输入的模型，用户侧以
	// available_model_flags（例如 "multimodal"）形式透出。
	MultimodalModels []string `json:"multimodal_models,omitempty"`
}
