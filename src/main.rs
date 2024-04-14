
use std::path::PathBuf;
use structopt::StructOpt;


#[derive(Debug, StructOpt)]
enum Subcommand {

    #[structopt(name = "why-depends")]
    WHY_DEPENDS {
        #[structopt(long = "errors-only", default = false)]
        errors_only: bool,

        #[structopt(name = "modid", required = true)]
        modid: String,
    },

    #[structopt(name = "find-error")]
    FIND_ERROR {
        #[structopt()]
    },

    #[structopt(name = "mod-info")]
    MOD_INFO = {
        #[structopt(name = "")]
    }

    #[structopt(name = "clean")]
    CLEAN {
        
    },
}

#[derive(Debug, StructOpt)]
#[structopt(name = "mc-packer", about = "A tool for validating minecraft mods and modpacks")]
struct SharedOpt {

    // used to increase ease of development
    #[structopt(short, long)]
    debug: bool,

    // comma-separated version overrides for modids
    // eg: "--override-versions minecraft=1.20.1,forge=47.1.101,neoforge=20.1"
    #[structopt(long = "override-versions")]
    overrides: Option<String>,

    // comma-separated modids: tell these mods that their dependencies are met
    // eg: "create_central_kitchen,createrailwaysnavigator,chefsdelight"
    #[structopt(long = "lie-depends")]
    lie_mods: Option<String>,

    // directory of modded minecraft profile
    #[structopt(parse(from_os_str))]
    profile_dir: PathBuf,

    // subcommand
}

fn main() {
    println!("Hello, world!");


}
