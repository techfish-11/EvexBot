use serde::Deserialize;
use std::fs;
use anyhow::Result;

#[derive(Debug, Deserialize, Clone)]
pub struct Config {
    pub prefix: String,
}

impl Config {
    pub fn load_from_file(path: &str) -> Result<Self> {
        let s = fs::read_to_string(path)?;
        let cfg: Config = serde_yaml::from_str(&s)?;
        Ok(cfg)
    }
}
