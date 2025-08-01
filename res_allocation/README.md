## Resource allocation schemes
Included schemes (in the `allocators` directory)
- `static`: static allocation (the baseline)
- `spirit`: Spirit's Symbiosis allocator (PTAS-based price search in `ptas_algorithm.py`)
- `oracle`: `Ideal` in the paper
- `inc_trade`: `Harvest` in the paper, which harvests resources from the best performing applications and redistribute them to struggling applications
- `fij_trade`: `Trade` in the paper, which trades resources directly between two applications, leveraging Spirit's $f_i$ estimation.
