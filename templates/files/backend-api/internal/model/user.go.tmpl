// Package model 定义领域实体（GORM struct）。
package model

import "time"

// User 通用用户实体。脚手架默认字段：
//   - UUID 业务主键（对外暴露），由服务端生成
//   - Email 登录账号
//   - PasswordHash bcrypt 哈希
//   - MemberLevel 会员等级
//
// 业务侧可在此模型上追加字段（注意保持与 init.sql 同步）。
type User struct {
	ID           uint      `gorm:"primaryKey" json:"-"`
	UUID         string    `gorm:"type:varchar(64);uniqueIndex;not null" json:"uuid"`
	Email        string    `gorm:"type:varchar(128);uniqueIndex;not null" json:"email"`
	PasswordHash string    `gorm:"type:varchar(255);not null" json:"-"`
	Nickname     string    `gorm:"type:varchar(64)" json:"nickname"`
	MemberLevel  string    `gorm:"type:varchar(32);default:'FREE'" json:"memberLevel"`
	CreatedAt    time.Time `json:"createdAt"`
	UpdatedAt    time.Time `json:"updatedAt"`
}

// TableName 显式指定表名。
func (User) TableName() string { return "users" }
