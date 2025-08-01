# draw_performance_disagg_2app.py  – 2-application version with cleaner layout
import os, itertools, numpy as np, pandas as pd, seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
from utils.logtools import get_latest_subdirectory, parse_log_file, parse_cont_logs

# ─────────────────────────── configuration ────────────────────────────
perf_metric     = 'perf'
use_all_subdirs = False
time_interval   = 30.0
target_cache_bw = [5120, 5120]

base_dir        = './logs/mindv2'
static_dir_base = f'{base_dir}/static'
inc_trade_base  = f'{base_dir}/inc-trade'
fij_trade_base  = f'{base_dir}/fij-trade'
oracle_dir_base = f'{base_dir}/oracle'
spirit_dir_base = f'{base_dir}/spirit'

directories = [
    f'{static_dir_base}/App2_int_{time_interval:.1f}sec',
    f'{inc_trade_base}/App2_int_{10:.1f}sec',
    f'{fij_trade_base}/App2_int_{time_interval:.1f}sec',
    f'{oracle_dir_base}/App2_int_{time_interval:.1f}sec',
    f'{spirit_dir_base}/App2_int_{time_interval:.1f}sec',
]
labels = ['Static', 'Harvest', 'Trade', 'Ideal', 'Spirit']

user_to_app = {1: "Stream", 2: "Memcached"}
user_ids    = [[1], [2]]                       # one subplot per app
cont_logs   = {1: 'spirit_stream_1.log',
               2: 'spirit_mc_client_2.log'}

# ───────────────────────── file helpers (unchanged) ───────────────────
def process_directory(directory, file_id, target_cache_bw, cont_logs):
    if use_all_subdirs:
        log_files = [os.path.join(r, f)
                     for r,_,fs in os.walk(directory)
                     for f in fs if f.endswith('.log')]
    else:
        latest   = get_latest_subdirectory(directory, target_cache_bw)
        log_files = [os.path.join(latest, f) for f in os.listdir(latest)
                     if f.endswith('.log')]
    log_files.sort()

    all_rows, cont_rows = [], []
    for p in log_files:
        name = os.path.basename(p)
        if name in cont_logs.values():
            app = next(k for k,v in cont_logs.items() if v == name)
            cont_rows.extend(parse_cont_logs(p, file_id, app))
        else:
            all_rows.extend(parse_log_file(p, file_id, 5*5000,
                                           include_use=True,
                                           perf_metric=perf_metric))
    return all_rows, cont_rows

def process_directories(dirs, tbw, c_logs):
    rows, cont = [], []
    for fid, d in enumerate(dirs):
        r, c = process_directory(d, fid, tbw, c_logs)
        rows.extend(r); cont.extend(c)
    df  = pd.DataFrame(rows,
          columns=['idx','uid','perf','cache','bw','fid'])
    dfc = pd.DataFrame(cont,
          columns=['idx','uid','perf','fid'])
    return df, dfc

# ───────────────────────── plotting helpers ───────────────────────────
def y_axis_limits(vals):
    if not vals: return (-25, 25, np.arange(-25, 26, 5))
    lo, hi = min(vals), max(vals)
    lo = min(lo, 0); hi = max(hi, 0)
    rng = max(hi - lo, 20)
    tick = 5 if rng <= 20 else 10 if rng <= 40 else 25
    lo = np.floor(lo / tick)*tick
    hi = np.ceil( hi / tick)*tick
    ticks = np.arange(lo, hi+tick, tick)
    return lo, hi, ticks

# ──────────────────────────── main plot ───────────────────────────────
def plot(df, dfc):
    plt.rcParams.update({'font.size': 12})

    # base performance (static)
    base = {uid: dfc[(dfc.uid==uid)&(dfc.fid==0)].perf.mean()
            for uid in user_to_app}

    fig_w = 3.2 * len(user_ids)         # 3.2″ per subplot
    fig_h = 3.0
    fig, axes = plt.subplots(1, len(user_ids),
                             figsize=(fig_w, fig_h),
                             sharey=False)
    if len(user_ids)==1: axes=[axes]

    palette = sns.color_palette('PuBu', len(labels))
    colors  = {i:c for i,c in enumerate(palette)}
    hatches = {i:h for i,h in zip(range(len(labels)),
               itertools.cycle(['//','\\\\','||','--','xx']))}

    for ax, group in zip(axes, user_ids):
        xpos, xlab, yvals = 0, [], []
        for uid in group:
            start = xpos                       # where this app’s bars begin
            for fid in range(1, len(labels)):  # skip Static=0
                vals = dfc[(dfc.uid==uid)&(dfc.fid==fid)].perf
                if vals.empty: continue
                rel = vals.mean()/base[uid]*100 - 100
                yvals.append(rel)
                ax.bar(xpos, rel,
                       color=colors[fid], hatch=hatches[fid],
                       edgecolor='white',
                       label=labels[fid] if uid==group[-1] and ax == axes[-1] else None)
                xpos += 1

            # centre tick under this cluster
            cluster_width = xpos - start           # number of bars we just drew
            xlab.append(start + (cluster_width-1)/2.0)

        ax.axhline(0, color='red', lw=1, ls='--')
        ax.set_xticks(xlab)
        ax.set_xticklabels([user_to_app[u] for u in group])
        lo, hi, ticks = y_axis_limits(yvals)
        ax.set_ylim(lo, hi); ax.set_yticks(ticks)
        ax.yaxis.set_minor_locator(MultipleLocator(25))
        if ax is axes[0]:
            ax.set_ylabel('E2E perf. Δ (%)')

    # ── leave a 10 % strip at top for the legend ───────────────────────
    # fig.tight_layout(rect=[0, 0, 1, 0.85])        # keep 90 % for axes
    #                                               # (left, bottom, right, top)
    # centred legend in the reserved strip
    handles, labs = axes[-1].get_legend_handles_labels()
    fig.legend(handles, labs, ncol=len(labels)-1,
               loc='upper center', bbox_to_anchor=(0.5, 0.965),
               frameon=False, columnspacing=0.8, handlelength=1.2)

    plt.tight_layout()
    plt.subplots_adjust(left=0.15, bottom=0.1, right=0.99, top=0.8, wspace=0.2)

    filename = 'disagg_performance_per_app_type_2app.pdf'
    plt.savefig(filename)
    print(f'Plot saved as {filename}')

# ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    df, df_c = process_directories(directories, target_cache_bw, cont_logs)
    plot(df, df_c)
