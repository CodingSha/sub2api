package service

import (
	"bytes"
	"encoding/json"
	"strings"

	"github.com/Wei-Shaw/sub2api/internal/pkg/apicompat"
	"github.com/tidwall/gjson"
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

func appendOpenAIAuditText(builder *strings.Builder, data []byte) {
	if builder == nil || len(data) == 0 || bytes.Equal(bytes.TrimSpace(data), []byte("[DONE]")) {
		return
	}

	eventType := strings.TrimSpace(gjson.GetBytes(data, "type").String())
	switch eventType {
	case "response.output_text.delta", "response.reasoning_summary_text.delta":
		if text := gjson.GetBytes(data, "delta").String(); text != "" {
			_, _ = builder.WriteString(text)
		}
	case "response.output_text.done", "response.reasoning_summary_text.done":
		appendOpenAIAuditSnapshotText(builder, gjson.GetBytes(data, "text").String())
	case "response.content_part.done", "response.reasoning_summary_part.done":
		appendOpenAIContentAuditText(builder, gjson.GetBytes(data, "part"))
	case "response.output_item.done":
		appendOpenAIOutputItemAuditText(builder, gjson.GetBytes(data, "item"))
	case "response.completed", "response.done", "response.incomplete", "response.cancelled", "response.canceled":
		if builder.Len() == 0 {
			appendOpenAIOutputAuditText(builder, gjson.GetBytes(data, "response.output"))
			appendOpenAIOutputAuditText(builder, gjson.GetBytes(data, "output"))
		}
	}
	if eventType == "" {
		if text := gjson.GetBytes(data, "content").String(); text != "" {
			_, _ = builder.WriteString(text)
		}
		if text := gjson.GetBytes(data, "text").String(); text != "" {
			_, _ = builder.WriteString(text)
		}
	}

	for _, choice := range gjson.GetBytes(data, "choices").Array() {
		if text := choice.Get("delta.content").String(); text != "" {
			_, _ = builder.WriteString(text)
		}
		if text := choice.Get("message.content").String(); text != "" {
			_, _ = builder.WriteString(text)
		}
	}
}

func appendOpenAIOutputAuditText(builder *strings.Builder, value gjson.Result) {
	if builder == nil || !value.Exists() {
		return
	}
	for _, item := range value.Array() {
		appendOpenAIOutputItemAuditText(builder, item)
	}
}

func appendOpenAIOutputItemAuditText(builder *strings.Builder, item gjson.Result) {
	if builder == nil || !item.Exists() {
		return
	}
	if text := item.Get("text").String(); text != "" {
		appendOpenAIAuditSnapshotText(builder, text)
	}
	for _, content := range item.Get("content").Array() {
		appendOpenAIContentAuditText(builder, content)
	}
}

func appendOpenAIContentAuditText(builder *strings.Builder, content gjson.Result) {
	if builder == nil || !content.Exists() {
		return
	}
	if text := content.Get("text").String(); text != "" {
		appendOpenAIAuditSnapshotText(builder, text)
	}
	if text := content.Get("content").String(); text != "" {
		appendOpenAIAuditSnapshotText(builder, text)
	}
}

func appendOpenAIAuditSnapshotText(builder *strings.Builder, text string) {
	if builder == nil || text == "" {
		return
	}
	current := builder.String()
	if current == "" {
		_, _ = builder.WriteString(text)
		return
	}
	if strings.HasSuffix(current, text) {
		return
	}
	max := len(text)
	if len(current) < max {
		max = len(current)
	}
	for size := max; size > 0; size-- {
		if strings.HasSuffix(current, text[:size]) {
			_, _ = builder.WriteString(text[size:])
			return
		}
	}
	_, _ = builder.WriteString(text)
}
