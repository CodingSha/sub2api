package service

import (
	"sort"
	"strings"
)

func normalizeGroupModelsListConfig(cfg GroupModelsListConfig) GroupModelsListConfig {
	out := GroupModelsListConfig{Enabled: cfg.Enabled}
	out.Models = normalizeModelIDList(cfg.Models)
	out.MultimodalModels = normalizeModelIDList(cfg.MultimodalModels)
	return out
}

func (g *Group) CustomModelsListEnabled() bool {
	return g != nil && g.ModelsListConfig.Enabled && len(g.ModelsListConfig.Models) > 0
}

func availableModelsFromAccountMappings(accounts []Account, platform string) []string {
	platform = strings.TrimSpace(platform)
	modelSet := make(map[string]struct{})
	for i := range accounts {
		acc := &accounts[i]
		if platform != "" && acc.Platform != platform {
			continue
		}
		for model := range acc.GetModelMapping() {
			model = strings.TrimSpace(model)
			if model == "" {
				continue
			}
			modelSet[model] = struct{}{}
		}
	}

	if len(modelSet) == 0 {
		return nil
	}
	models := make([]string, 0, len(modelSet))
	for model := range modelSet {
		models = append(models, model)
	}
	sort.Strings(models)
	return models
}

func hasUnrestrictedAccountForPlatform(accounts []Account, platform string) bool {
	platform = strings.TrimSpace(platform)
	for i := range accounts {
		acc := &accounts[i]
		if platform != "" && acc.Platform != platform {
			continue
		}
		if len(acc.GetModelMapping()) == 0 {
			return true
		}
	}
	return false
}

func mergeModelLists(primary []string, extra []string) []string {
	seen := make(map[string]struct{}, len(primary)+len(extra))
	out := make([]string, 0, len(primary)+len(extra))
	for _, models := range [][]string{primary, extra} {
		for _, model := range models {
			model = strings.TrimSpace(model)
			if model == "" {
				continue
			}
			if _, ok := seen[model]; ok {
				continue
			}
			seen[model] = struct{}{}
			out = append(out, model)
		}
	}
	return out
}

const ModelFlagMultimodal = "multimodal"

func availableModelFlagsFromConfig(models []string, cfg GroupModelsListConfig) map[string][]string {
	if len(models) == 0 {
		return nil
	}
	multimodalModels := normalizeModelIDList(cfg.MultimodalModels)
	if len(multimodalModels) == 0 {
		return nil
	}
	multimodalSet := make(map[string]struct{}, len(multimodalModels))
	for _, model := range multimodalModels {
		multimodalSet[model] = struct{}{}
	}
	out := make(map[string][]string)
	for _, model := range models {
		model = strings.TrimSpace(model)
		if model == "" {
			continue
		}
		if _, ok := multimodalSet[model]; ok {
			out[model] = []string{ModelFlagMultimodal}
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func normalizeModelIDList(models []string) []string {
	if len(models) == 0 {
		return nil
	}
	seen := make(map[string]struct{}, len(models))
	out := make([]string, 0, len(models))
	for _, model := range models {
		model = strings.TrimSpace(model)
		if model == "" {
			continue
		}
		if _, ok := seen[model]; ok {
			continue
		}
		seen[model] = struct{}{}
		out = append(out, model)
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
