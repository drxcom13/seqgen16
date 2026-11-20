-- =============================================================================
-- tb_seq_gen16.vhd - samodzielna weryfikacja seq_gen16 (bez Nios), tylko przez porty
--   Questa:  cd sim ; vsim -c -do run_sim.do
--   GHDL:    ghdl -a --std=08 ../quartus/ip/seq_gen16/seq_channel.vhd ../quartus/ip/seq_gen16/seq_gen16.vhd tb_seq_gen16.vhd
--            ghdl -r --std=08 tb_seq_gen16
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_seq_gen16 is
end entity;

architecture sim of tb_seq_gen16 is

    constant TCLK : time := 20 ns;          -- 50 MHz
    constant TUS  : time := 1 us;

    signal clk       : std_logic := '0';
    signal reset     : std_logic := '1';
    signal address   : std_logic_vector(14 downto 0) := (others => '0');
    signal read      : std_logic := '0';
    signal write     : std_logic := '0';
    signal writedata : std_logic_vector(31 downto 0) := (others => '0');
    signal readdata  : std_logic_vector(31 downto 0);
    signal ch_out    : std_logic_vector(15 downto 0);
    signal ch_run    : std_logic_vector(15 downto 0);
    signal clk_base  : std_logic;
    signal sync_out  : std_logic;
    -- czas ostatniego zbocza (porownanie czasu symulacji, niezalezne od delt)
    signal t_ch5, t_bclk, t_sync : time := 0 ns;

    signal errors    : integer := 0;
    signal sync_err  : integer := 0;
    signal sync_chk  : boolean := false;

    function creg(ch, r : integer) return integer is
    begin
        return 16#40# + ch * 4 + r;
    end function;

    function smp(ch, i : integer) return integer is
    begin
        return 16#4000# + ch * 1024 + i;
    end function;

begin

    clk <= not clk after TCLK / 2;

    dut : entity work.seq_gen16
        generic map (CLK_HZ => 50000000)
        port map (
            clk => clk, reset => reset,
            avs_address => address, avs_read => read, avs_write => write,
            avs_writedata => writedata, avs_readdata => readdata,
            ch_out => ch_out, ch_running => ch_run,
            clk_base => clk_base, sync_out => sync_out
        );

    p_t_ch5  : process (ch_out(5)) begin t_ch5  <= now; end process;
    p_t_bclk : process (clk_base)  begin t_bclk <= now; end process;
    p_t_sync : process (sync_out)  begin t_sync <= now; end process;

    -- zgodnosc kanalow 1 i 2 (ta sama konfiguracja, wspolny start) - co takt
    p_sync : process (clk)
    begin
        if rising_edge(clk) then
            if sync_chk and ch_out(1) /= ch_out(2) then
                sync_err <= sync_err + 1;
            end if;
        end if;
    end process;

    p_main : process

        procedure wr(a : integer; d : integer) is
        begin
            wait until rising_edge(clk);
            address   <= std_logic_vector(to_unsigned(a, 15));
            writedata <= std_logic_vector(to_signed(d, 32));
            write     <= '1';
            wait until rising_edge(clk);
            write     <= '0';
        end procedure;

        procedure rd(a : integer; d : out std_logic_vector(31 downto 0)) is
        begin
            wait until rising_edge(clk);
            address <= std_logic_vector(to_unsigned(a, 15));
            read    <= '1';
            wait until rising_edge(clk);
            read    <= '0';
            wait until rising_edge(clk);     -- latencja 2
            wait for 1 ns;
            d := readdata;
        end procedure;

        procedure check(what : string; got, exp : integer) is
        begin
            if got /= exp then
                report "BLAD " & what & ": jest " & integer'image(got) & ", oczekiwano " & integer'image(exp)
                    severity error;
                errors <= errors + 1;
                wait for 0 ns;
            end if;
        end procedure;

        variable d     : std_logic_vector(31 downto 0);
        variable t0    : time;
        type int_arr is array (natural range <>) of integer;
        -- ch0: probki 0,1,0,1,0 ; obserwacja od kroku 1 (granica taktu 1 us):
        -- kroki 1..4, potem 2. przebieg 0..4, potem idle = 0
        constant EXP0 : int_arr(0 to 10) := (1, 0, 1, 0, 0, 1, 0, 1, 0, 0, 0);
        constant PAT0 : int_arr(0 to 4)  := (0, 1, 0, 1, 0);

    begin
        wait for 5 * TCLK;
        wait until rising_edge(clk);
        reset <= '0';
        wait for 3 * TCLK;

        -- ------------------------------------------------ 1. identyfikacja
        rd(0, d); check("ID", to_integer(unsigned(d(30 downto 0))), 16#53513136#);
        rd(5, d); check("CLK_HZ", to_integer(unsigned(d(30 downto 0))), 50000000);
        rd(6, d); check("PARAMS NCH", to_integer(unsigned(d(7 downto 0))), 16);
        check("PARAMS AW", to_integer(unsigned(d(15 downto 8))), 10);
        check("PARAMS bit/krok", to_integer(unsigned(d(23 downto 16))), 1);
        check("PARAMS PW", to_integer(unsigned(d(31 downto 24))), 20);

        -- ------------------------------------------------ 2. pamiec 1024 probek
        for i in 0 to 1023 loop
            wr(smp(0, i), i mod 2);
            wr(smp(15, i), (i / 3) mod 2);
        end loop;
        for i in 0 to 1023 loop
            if i mod 97 = 0 then
                rd(smp(0, i), d);  check("RAM ch0",  to_integer(unsigned(d)), i mod 2);
                rd(smp(15, i), d); check("RAM ch15", to_integer(unsigned(d)), (i / 3) mod 2);
            end if;
        end loop;

        -- ------------------------------------------------ 3. sekwencja ch0 (tryb cyfrowy)
        for i in 0 to 4 loop wr(smp(0, i), PAT0(i)); end loop;
        wr(creg(0, 0), 3);                                  -- 4 us na krok
        wr(creg(0, 1), 4);                                  -- 5 krokow
        wr(creg(0, 2), 16#02000000# + 2);                   -- idle_on_done, idle=0, 2 powtorzenia
        wr(1, 16#0001#);                                    -- START ch0
        wait until rising_edge(ch_out(0)) for 20 us;
        check("ch0 zbocze narastajace (poczatek kroku 1)", boolean'pos(ch_out(0) = '1'), 1);
        t0 := now;
        for k in EXP0'range loop
            for s in 0 to 3 loop
                wait for (t0 + (k * 4 + s) * TUS + TUS / 2) - now;
                check("ch0 krok " & integer'image(k + 1) & " probka " & integer'image(s),
                      boolean'pos(ch_out(0) = '1'), EXP0(k));
            end loop;
        end loop;
        rd(3, d); check("RUNNING ch0 po zakonczeniu", to_integer(unsigned(d(0 downto 0))), 0);
        rd(4, d); check("DONE ch0", to_integer(unsigned(d(0 downto 0))), 1);
        rd(creg(0, 3), d);
        check("STATUS ch0 index", to_integer(unsigned(d(9 downto 0))), 4);
        check("STATUS ch0 done",  to_integer(unsigned(d(17 downto 17))), 1);

        -- ------------------------------------------------ 4. synchronizacja ch1/ch2
        for i in 0 to 7 loop
            wr(smp(1, i), (i * 5) mod 3 mod 2);
            wr(smp(2, i), (i * 5) mod 3 mod 2);
        end loop;
        for c in 1 to 2 loop
            wr(creg(c, 0), 1); wr(creg(c, 1), 7); wr(creg(c, 2), 0);
        end loop;
        sync_chk <= true;
        wr(1, 16#0006#);                                    -- START ch1+ch2 jednym zapisem
        wait for 100 us;
        rd(3, d); check("RUNNING ch1|ch2", to_integer(unsigned(d(2 downto 1))), 3);
        check("rozbieznosc wyjsc ch1/ch2 [takty]", sync_err, 0);

        -- ------------------------------------------------ 5. STOP
        wr(2, 16#0006#);
        wait for 5 * TCLK;
        sync_chk <= false;
        rd(3, d); check("RUNNING po STOP", to_integer(unsigned(d(2 downto 1))), 0);

        -- ------------------------------------------------ 6. poziom spoczynkowy (idle) i STATUS[24]
        wr(creg(4, 2), 16#00010000#);                       -- idle = 1
        wait for 5 * TCLK;
        check("idle = 1 na wyjsciu", boolean'pos(ch_out(4) = '1'), 1);
        rd(creg(4, 3), d); check("STATUS[24] = wyjscie", to_integer(unsigned(d(24 downto 24))), 1);
        wr(creg(4, 2), 0);                                  -- idle = 0
        wait for 5 * TCLK;
        check("idle = 0 na wyjsciu", boolean'pos(ch_out(4) = '1'), 0);

        -- ------------------------------------------------ 7. krok 1 ms
        wr(smp(3, 0), 0); wr(smp(3, 1), 1); wr(smp(3, 2), 0);
        wr(creg(3, 0), 999);                                -- 1 ms
        wr(creg(3, 1), 2);
        wr(creg(3, 2), 1);                                  -- 1 raz, potem trzymaj ostatni (0)
        wr(1, 16#0008#);
        wait until rising_edge(ch_out(3)) for 3 ms;
        t0 := now;
        wait until falling_edge(ch_out(3)) for 3 ms;
        check("krok 1 ms [ns]", (now - t0) / 1 ns, 1000000);

        -- ------------------------------------------------ 8. BASE_CLK i SYNC wyrownane z wyjsciami
        wr(2, 16#FFFF#);                                    -- STOP wszystkich
        for i in 0 to 7 loop wr(smp(5, i), 1 - (i mod 2)); end loop;   -- 1,0,1,0...
        wr(creg(5, 0), 1);                                  -- krok 2 us
        wr(creg(5, 1), 7);
        wr(creg(5, 2), 0);                                  -- petla, idle 0
        wr(7, 1);                                           -- BASE_CLK: polokres 2 us -> okres 4 us
        wr(8, 3);                                           -- SYNC: 3 us
        wr(1, 16#0020#);                                    -- START ch5
        wait until rising_edge(sync_out) for 10 us;
        t0 := now;
        check("SYNC zbocze w chwili startu", boolean'pos(sync_out = '1'), 1);
        wait for 1 ns;
        check("ch5 zbocze - SYNC zbocze [ns]", (t_ch5 - t_sync) / 1 ns, 0);
        check("BASE_CLK zbocze - SYNC zbocze [ns]", (t_bclk - t_sync) / 1 ns, 0);
        check("BASE_CLK = 1 po starcie", boolean'pos(clk_base = '1'), 1);
        wait until falling_edge(sync_out) for 10 us;
        check("szerokosc SYNC [ns]", (now - t0) / 1 ns, 3000);
        wait until rising_edge(clk_base) for 10 us;
        check("okres BASE_CLK [ns]", (now - t0) / 1 ns, 4000);
        wait for 1 ns;
        check("ch5 zbocze krok 2 - BASE_CLK zbocze [ns]", (t_ch5 - t_bclk) / 1 ns, 0);
        check("ch5 krok 2 = 1", boolean'pos(ch_out(5) = '1'), 1);
        wr(8, 0);                                           -- SYNC = dopoki pracuje kanal
        wait for 1 us;
        check("SYNC tryb 'running'", boolean'pos(sync_out = '1'), 1);
        -- restart w trakcie pracy, gdy BASE_CLK = 1 -> i tak zbocze narastajace w chwili startu
        wait until rising_edge(clk_base) for 10 us;
        wait for 1 us;                                      -- BASE_CLK = 1 (polokres 2 us)
        check("BASE_CLK = 1 przed restartem", boolean'pos(clk_base = '1'), 1);
        wr(1, 16#0020#);                                    -- ponowny START ch5
        wait until rising_edge(sync_out) for 10 us;
        wait for 1 ns;
        check("restart: BASE_CLK zbocze - SYNC zbocze [ns]", (t_bclk - t_sync) / 1 ns, 0);
        -- ch5 byl juz w stanie 1, a krok 0 = 1 -> brak zbocza; sprawdzamy stan i nastepne zbocze
        check("restart: ch5 = krok 0 (1)", boolean'pos(ch_out(5) = '1'), 1);
        t0 := t_sync;
        wait until falling_edge(ch_out(5)) for 10 us;
        check("restart: ch5 krok 1 po 2 us [ns]", (now - t0) / 1 ns, 2000);
        wr(2, 16#0020#);
        wait for 1 us;
        check("SYNC po STOP", boolean'pos(sync_out = '0'), 1);
        check("BASE_CLK = 0 w spoczynku", boolean'pos(clk_base = '0'), 1);

        -- ------------------------------------------------ 9. minimalny krok 1 us (PERIOD = 0)
        for i in 0 to 7 loop wr(smp(6, i), i mod 2); end loop;          -- 0,1,0,1...
        wr(creg(6, 0), 0);                                  -- 1 us
        wr(creg(6, 1), 7);
        wr(creg(6, 2), 1);                                  -- 1 raz
        wr(1, 16#0040#);
        wait until rising_edge(ch_out(6)) for 5 us;
        t0 := now;
        wait until falling_edge(ch_out(6)) for 5 us;
        check("krok 1 us [ns]", (now - t0) / 1 ns, 1000);
        wait until rising_edge(ch_out(6)) for 5 us;
        check("okres 2 krokow po 1 us [ns]", (now - t0) / 1 ns, 2000);

        -- ------------------------------------------------
        wait for 1 us;
        if errors = 0 then
            report "=== TEST PASSED ===" severity note;
        else
            report "=== TEST FAILED: " & integer'image(errors) & " bledow ===" severity error;
        end if;
        std.env.finish;
    end process;

end architecture;
