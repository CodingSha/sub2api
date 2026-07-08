package service

import (
	"encoding/json"
	"strings"

	"github.com/Wei-Shaw/sub2api/internal/pkg/apicompat"
)

func appendAnthropicAuditData(builder *strings.Builder, data string) {
	if builder == nil {
		return
	}
	data = strings.TrimSpace(data)
	if data == "" || data == "[DONE]" {
		return
	}
	var event map[string]any
	if err := json.Unmarshal([]byte(data), &event); err != nil {
		return
	}
	eventType, _ := event["type"].(string)
	appendAnthropicStreamEventAuditText(builder, mapToAnthropicStreamEvent(eventType, event))
}

func appendChatChunkAuditText(builder *strings.Builder, chunk apicompat.ChatCompletionsChunk) {
	if builder == nil {
		return
	}
	for _, choice := range chunk.Choices {
		if choice.Delta.Content != nil {
			_, _ = builder.WriteString(*choice.Delta.Content)
		}
		if choice.Delta.ReasoningContent != nil {
			_, _ = builder.WriteString(*choice.Delta.ReasoningContent)
		}
	}
}

func mapToAnthropicStreamEvent(eventType string, event map[string]any) *apicompat.AnthropicStreamEvent {
	if event == nil {
		return nil
	}
	raw, err := json.Marshal(event)
	if err != nil {
		return nil
	}
	var parsed apicompat.AnthropicStreamEvent
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil
	}
	if parsed.Type == "" {
		parsed.Type = eventType
	}
	return &parsed
}
