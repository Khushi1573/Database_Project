-- lua/mixed_rw.lua (FR-8, FR-9, AC-5)
--
-- sysbench 1.0.x custom script for the mixed read/write workload used in
-- all three phases of each run (95% / 10% / 95% reads). The read:write
-- split per event is controlled by --read_pct (0-100), sampled per event.
--
-- Read event:  SELECT payload FROM sbtest1 WHERE k = ?      (equality lookup on k)
-- Write event: UPDATE sbtest1 SET k = ? WHERE id = ?        (exercises idx_k maintenance)

pcall(function() require("oltp_common") end)

sysbench.cmdline.options = {
    read_pct = {"Percentage of events that are reads (0-100)", 95},
    table_size = {"Number of rows in sbtest1", 1000000},
}

function thread_init()
    drv = sysbench.sql.driver()
    con = drv:connect()
end

function thread_done()
    if con then
        con:disconnect()
    end
end

local function rand_id()
    return sysbench.rand.uniform(1, sysbench.opt.table_size)
end

local function do_read()
    local k = rand_id()
    con:query(string.format("SELECT payload FROM sbtest1 WHERE k = %d", k))
end

local function do_write()
    local new_k = rand_id()
    local id = rand_id()
    con:query(string.format("UPDATE sbtest1 SET k = %d WHERE id = %d", new_k, id))
end

function event()
    local roll = sysbench.rand.uniform(1, 100)
    if roll <= sysbench.opt.read_pct then
        do_read()
    else
        do_write()
    end
end
