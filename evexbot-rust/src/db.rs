use sqlx::{Sqlite, SqlitePool, Row};
use serenity::http::Http;
use std::sync::Arc;
use anyhow::Result;
use once_cell::sync::OnceCell;

static POOL: OnceCell<Arc<SqlitePool>> = OnceCell::new();

pub async fn init_db(http: &Http) -> Result<()> {
    // Use a data directory similar to the Python project
    let db_path = std::path::Path::new("data/welcome.db");
    if let Some(parent) = db_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let url = format!("sqlite:{}", db_path.to_string_lossy());
    let pool = SqlitePool::connect(&url).await?;

    // create tables
    sqlx::query("CREATE TABLE IF NOT EXISTS welcome_settings (
        guild_id INTEGER PRIMARY KEY,
        is_enabled INTEGER DEFAULT 0,
        member_increment INTEGER DEFAULT 100,
        channel_id INTEGER DEFAULT NULL
    );")
    .execute(&pool)
    .await?;

    sqlx::query("CREATE TABLE IF NOT EXISTS leave_settings (
        guild_id INTEGER PRIMARY KEY,
        is_enabled INTEGER DEFAULT 0,
        channel_id INTEGER DEFAULT NULL
    );")
    .execute(&pool)
    .await?;

    POOL.set(Arc::new(pool)).ok();
    Ok(())
}

fn pool() -> Arc<SqlitePool> {
    POOL.get().expect("DB pool not initialized").clone()
}

pub async fn get_welcome_settings(guild_id: i64) -> Result<(bool, i64, Option<i64>)> {
    let pool = pool();
    let row = sqlx::query("SELECT is_enabled, member_increment, channel_id FROM welcome_settings WHERE guild_id = ?")
        .bind(guild_id)
        .fetch_optional(&*pool)
        .await?;

    if let Some(r) = row {
        Ok((r.get::<i64, _>(0) != 0, r.get::<i64, _>(1), r.try_get::<i64, _>(2).ok()))
    } else {
        Ok((false, 100, None))
    }
}

pub async fn update_welcome_settings(guild_id: i64, is_enabled: bool, member_increment: Option<i64>, channel_id: Option<i64>) -> Result<()> {
    let pool = pool();
    sqlx::query("INSERT INTO welcome_settings (guild_id, is_enabled, member_increment, channel_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            is_enabled=excluded.is_enabled,
            member_increment=COALESCE(?, welcome_settings.member_increment),
            channel_id=COALESCE(?, welcome_settings.channel_id)")
        .bind(guild_id)
        .bind(is_enabled as i64)
        .bind(member_increment.unwrap_or(100))
        .bind(channel_id)
        .bind(member_increment)
        .bind(channel_id)
        .execute(&*pool)
        .await?;
    Ok(())
}

pub async fn get_leave_settings(guild_id: i64) -> Result<(bool, Option<i64>)> {
    let pool = pool();
    let row = sqlx::query("SELECT is_enabled, channel_id FROM leave_settings WHERE guild_id = ?")
        .bind(guild_id)
        .fetch_optional(&*pool)
        .await?;

    if let Some(r) = row {
        Ok((r.get::<i64, _>(0) != 0, r.try_get::<i64, _>(1).ok()))
    } else {
        Ok((false, None))
    }
}

pub async fn update_leave_settings(guild_id: i64, is_enabled: bool, channel_id: Option<i64>) -> Result<()> {
    let pool = pool();
    sqlx::query("INSERT INTO leave_settings (guild_id, is_enabled, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            is_enabled=excluded.is_enabled,
            channel_id=COALESCE(?, leave_settings.channel_id)")
        .bind(guild_id)
        .bind(is_enabled as i64)
        .bind(channel_id)
        .bind(channel_id)
        .execute(&*pool)
        .await?;
    Ok(())
}
