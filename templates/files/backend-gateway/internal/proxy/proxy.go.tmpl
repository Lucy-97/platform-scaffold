// Package proxy 提供反向代理转发，支持 SSE/binary 流式响应。
package proxy

import (
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

var sharedClient = &http.Client{
	Timeout: 120 * time.Second,
	Transport: &http.Transport{
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 50,
		IdleConnTimeout:     90 * time.Second,
	},
	CheckRedirect: func(req *http.Request, via []*http.Request) error {
		return http.ErrUseLastResponse
	},
}

// ForwardRequest 把请求透传到 targetBaseURL，并注入 X-Internal-Secret。
func ForwardRequest(targetBaseURL, internalSecret string) gin.HandlerFunc {
	return func(c *gin.Context) {
		upstream := targetBaseURL + c.Request.URL.Path
		if c.Request.URL.RawQuery != "" {
			upstream += "?" + c.Request.URL.RawQuery
		}
		req, err := http.NewRequestWithContext(c.Request.Context(), c.Request.Method, upstream, c.Request.Body)
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{"error": "failed to create upstream request"})
			return
		}
		for k, vs := range c.Request.Header {
			for _, v := range vs {
				req.Header.Add(k, v)
			}
		}
		if internalSecret != "" {
			req.Header.Set("X-Internal-Secret", internalSecret)
		}
		resp, err := sharedClient.Do(req)
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{"error": "upstream service unavailable"})
			return
		}
		defer resp.Body.Close()

		ct := resp.Header.Get("Content-Type")
		if strings.Contains(ct, "text/event-stream") || strings.Contains(ct, "audio/") || strings.Contains(ct, "application/octet-stream") {
			streamResponse(c, resp, ct)
			return
		}
		// 注意：用 Add 而非 Set，保留多值 Set-Cookie。
		for k, vs := range resp.Header {
			for _, v := range vs {
				c.Writer.Header().Add(k, v)
			}
		}
		c.Status(resp.StatusCode)
		_, _ = io.Copy(c.Writer, resp.Body)
	}
}

func streamResponse(c *gin.Context, resp *http.Response, ct string) {
	if strings.Contains(ct, "text/event-stream") {
		c.Header("Content-Type", "text/event-stream")
		c.Header("Cache-Control", "no-cache")
		c.Header("Connection", "keep-alive")
	} else {
		c.Header("Content-Type", ct)
	}
	c.Header("X-Accel-Buffering", "no")

	flusher, ok := c.Writer.(http.Flusher)
	if !ok {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "streaming not supported"})
		return
	}
	c.Status(resp.StatusCode)

	buf := make([]byte, 4096)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			_, _ = c.Writer.Write(buf[:n])
			flusher.Flush()
		}
		if err != nil {
			break
		}
	}
}
