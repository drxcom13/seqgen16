-- =============================================================================
-- proto_io.vhd  -  tester protokolow szeregowych (SPI / I2C / UART TTL), slave Avalon-MM
--
-- Magistrala: 32 bit, adresowanie slowami, readLatency = 2 (staly), zapis bez waitrequest.
--
-- Mapa rejestrow (offset w SLOWACH):
--   0  ID      R   0x50544F31 ("PTO1")
--   1  CTRL    RW  [2:0] tryb: 0 = wylaczony (wszystkie linie J6 wejsciami), 1 = SPI master,
--                        2 = I2C master, 3 = UART (4..7 zarezerwowane: slave / podsluch)
--                  [3] CPOL  [4] CPHA  [5] SPI LSB first
--                  [7:6] parzystosc UART: 0 = brak, 1 = parzysta, 2 = nieparzysta
--                  [8] UART 2 bity stopu
--                  [11:9] opoznienie probkowania MISO w taktach (0..7, kompensacja opoznienia
--                         translatorow i kabla; domyslnie 1)
--                  Kazdy zapis: silniki w stan spoczynku, kolejki TX/RX oproznione, flagi skasowane.
--   2  DIV     RW  SPI: polowa okresu SCK w taktach - 1 (1 -> 12,5 MHz, 2 -> 8,33 MHz ...)
--                  I2C: jednostka czasu bitu w taktach - 1, SCL = CLK / (5 * (DIV + 1)), stan niski 3/5
--                       (99 -> 100 kHz, 24 -> 400 kHz, 9 -> 1 MHz)
--                  UART: przyrost akumulatora fazy = round(16 * baud * 2^32 / CLK_HZ)
--   3  TXD     W   wpis do kolejki TX (512 slow, 10 bit):
--                  SPI:  [7:0] bajt, [8] = ostatni bajt transakcji (po nim CS = 1)
--                  I2C:  [9:8] = 00 zapis bajtu [7:0] (RX: [8] = NACK), 01 odczyt z ACK,
--                        10 odczyt z NACK (RX: bajt), 11 sterowanie: [0] START (lub powtorzony),
--                        [1] STOP
--                  UART: [7:0] bajt
--   4  RXD     R   odczyt z kolejki RX (512 slow, 9 bit): [8:0] wpis, [31] = 1 gdy wpis byl
--                  (odczyt pustej kolejki zwraca 0). SPI: bajt odebrany z MISO, I2C: patrz wyzej,
--                  UART: [7:0] bajt, [8] = blad ramki lub parzystosci
--   5  STATUS  R   [9:0] wpisy w TX, [19:10] wpisy w RX, [20] zajety (silnik pracuje lub TX
--                  niepusta), [21] przepelnienie RX, [22] blad magistrali I2C (timeout SCL ~21 ms),
--                  [23] TX pelna, [24] HOLD
--              W   [0] HOLD: 1 = silniki nie zaczynaja nowej transakcji (firmware laduje cala kolejke
--                  TX, potem HOLD = 0 -> transmisja bez przerw miedzy bajtami)
--   6  PVCC    RW  poziom J6 (LDO U13): [0] wlaczone [1] sel 2V5 [2] sel 3V3 [3] sel 5V.
--                  Kazdy zapis: LDO wylaczone na 2,5 ms (rozladowanie), potem en = [0].
--   7  PINS    R   stan linii J6 (po synchronizacji): [0] SCK [1] MOSI [2] CS [3] MISO [4] TX
--                  [5] RX [6] SDA [7] SCL; [31:8] licznik mikrosekund (24 bit, przepelnienie co 16,7 s)
--
-- Linie J6 (translatory SN74LVC1T45, DIR = 1: FPGA -> J6):
--   SPI master: SCK, MOSI, CS wyjscia (dir_spi = 1), MISO wejscie. UART: TX wyjscie, RX wejscie.
--   I2C: sda_drv / scl_drv = 1 sciaga linie do 0 (2N7002), stan z wejsc sda_i / scl_i.
--   Po resecie i w trybie 0 wszystkie translatory w kierunku J6 -> FPGA, drivery I2C wylaczone.
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity proto_io is
    generic (
        CLK_HZ : positive := 50000000
    );
    port (
        clk           : in  std_logic;
        reset         : in  std_logic;
        avs_address   : in  std_logic_vector(2 downto 0);
        avs_read      : in  std_logic;
        avs_write     : in  std_logic;
        avs_writedata : in  std_logic_vector(31 downto 0);
        avs_readdata  : out std_logic_vector(31 downto 0);
        -- linie J6 (strona FPGA)
        sck_o    : out std_logic;
        mosi_o   : out std_logic;
        cs_o     : out std_logic;
        miso_o   : out std_logic;
        tx_o     : out std_logic;
        dir_spi  : out std_logic;           -- SCK/MOSI/CS: 1 = wyjscia
        dir_miso : out std_logic;           -- MISO: 1 = wyjscie (slave, przyszlosc)
        dir_tx   : out std_logic;           -- TX: 1 = wyjscie
        sda_drv  : out std_logic;           -- 1 = SDA w stanie niskim
        scl_drv  : out std_logic;           -- 1 = SCL w stanie niskim
        pins_i   : in  std_logic_vector(7 downto 0);   -- SCK MOSI CS MISO TX RX SDA SCL (bity 0..7)
        pvcc_ctrl : out std_logic_vector(3 downto 0)   -- [0]=en [1]=2V5 [2]=3V3 [3]=5V
    );
end entity;

architecture rtl of proto_io is

    constant ID : std_logic_vector(31 downto 0) := x"50544F31";

    -- ------------------------------------------------------------ rejestry
    signal mode     : unsigned(2 downto 0) := (others => '0');
    signal cpol     : std_logic := '0';
    signal cpha     : std_logic := '0';
    signal lsb      : std_logic := '0';
    signal parity   : std_logic_vector(1 downto 0) := "00";
    signal stop2    : std_logic := '0';
    signal sdel     : unsigned(2 downto 0) := to_unsigned(1, 3);
    signal div      : unsigned(31 downto 0) := to_unsigned(99, 32);
    signal clr      : std_logic := '0';               -- impuls: zapis CTRL
    signal hold     : std_logic := '0';
    signal go       : std_logic;                      -- kolejka TX niepusta i brak HOLD
    constant US_DIV : positive := CLK_HZ / 1000000;
    signal us_pre   : integer range 0 to US_DIV - 1 := 0;
    signal us_cnt   : unsigned(23 downto 0) := (others => '0');

    -- ------------------------------------------------------------ kolejki
    constant FA : positive := 9;                      -- 512 wpisow
    signal tx_wa, tx_ra : unsigned(FA-1 downto 0) := (others => '0');
    signal rx_wa, rx_ra : unsigned(FA-1 downto 0) := (others => '0');
    signal tx_cnt, rx_cnt : unsigned(FA downto 0) := (others => '0');
    signal tx_q     : std_logic_vector(9 downto 0);
    signal rx_q     : std_logic_vector(8 downto 0);
    signal tx_push, tx_pop, rx_push, rx_pop, rx_we : std_logic;
    signal tx_wd    : std_logic_vector(9 downto 0);
    signal rx_wd    : std_logic_vector(8 downto 0);
    signal rx_ovf   : std_logic := '0';
    signal bus_err  : std_logic := '0';

    -- wspolne zadanie pobrania / wstawienia z silnikow
    signal spi_pop, i2c_pop, ua_pop       : std_logic;
    signal spi_push, i2c_push, ua_push    : std_logic;
    signal spi_wd, i2c_wd, ua_wd          : std_logic_vector(8 downto 0);
    signal spi_busy, i2c_busy, ua_busy    : std_logic;
    signal i2c_err                        : std_logic;

    -- ------------------------------------------------------------ wejscia
    signal pin_s1, pin_s2 : std_logic_vector(7 downto 0) := (others => '1');
    alias miso_s : std_logic is pin_s2(3);
    alias rx_s   : std_logic is pin_s2(5);
    alias sda_s  : std_logic is pin_s2(6);
    alias scl_s  : std_logic is pin_s2(7);

    -- ------------------------------------------------------------ SPI master
    type spi_st_t is (S_IDLE, S_LOAD, S_CSSU, S_BITS, S_WAIT, S_CSH, S_CSOFF);
    signal spi_st   : spi_st_t := S_IDLE;
    signal spi_cnt  : unsigned(15 downto 0) := (others => '0');
    signal spi_h    : unsigned(3 downto 0) := (others => '0');     -- polokres 0..15
    signal spi_sh   : std_logic_vector(7 downto 0) := (others => '0');
    signal spi_last : std_logic := '0';
    signal spi_sck  : std_logic := '0';
    signal spi_mosi : std_logic := '0';
    signal spi_cs   : std_logic := '1';
    signal spi_stb  : std_logic_vector(9 downto 0) := (others => '0');   -- linia opozniajaca probkowanie
    signal spi_rsh  : std_logic_vector(7 downto 0) := (others => '0');
    signal spi_rbit : unsigned(2 downto 0) := (others => '0');

    -- ------------------------------------------------------------ I2C master
    type i2c_st_t is (I_IDLE, I_LOAD, I_START, I_STOP, I_BIT, I_NEXT);
    signal i2c_st   : i2c_st_t := I_IDLE;
    signal i2c_q    : unsigned(1 downto 0) := (others => '0');     -- cwiartka bitu
    signal i2c_cnt  : unsigned(16 downto 0) := (others => '0');
    signal i2c_u1   : unsigned(16 downto 0);                       -- 1 jednostka czasu bitu - 1
    signal i2c_u2   : unsigned(16 downto 0);                       -- 2 jednostki - 1
    signal i2c_to   : unsigned(20 downto 0) := (others => '0');    -- timeout rozciagania SCL
    signal i2c_op   : std_logic_vector(1 downto 0) := "00";
    signal i2c_sh   : std_logic_vector(8 downto 0) := (others => '0');  -- 8 bitow + ACK
    signal i2c_bit  : unsigned(3 downto 0) := (others => '0');
    signal i2c_rd   : std_logic_vector(8 downto 0) := (others => '0');
    signal i2c_sda  : std_logic := '0';                             -- 1 = sciagaj SDA
    signal i2c_scl  : std_logic := '0';                             -- 1 = sciagaj SCL

    -- ------------------------------------------------------------ UART
    signal ua_acc   : unsigned(31 downto 0) := (others => '0');
    signal ua_tick  : std_logic := '0';                             -- 16 x baud
    type ua_tx_st_t is (T_IDLE, T_LOAD, T_SEND);
    signal ua_tst   : ua_tx_st_t := T_IDLE;
    signal ua_tsh   : std_logic_vector(11 downto 0) := (others => '1');
    signal ua_tn    : unsigned(3 downto 0) := (others => '0');      -- bity do wyslania
    signal ua_tt    : unsigned(3 downto 0) := (others => '0');      -- ticki w bicie
    signal ua_txo   : std_logic := '1';
    type ua_rx_st_t is (R_IDLE, R_START, R_BITS);
    signal ua_rst   : ua_rx_st_t := R_IDLE;
    signal ua_rt    : unsigned(3 downto 0) := (others => '0');
    signal ua_rn    : unsigned(3 downto 0) := (others => '0');
    signal ua_rsh   : std_logic_vector(10 downto 0) := (others => '0');
    signal ua_nbits : unsigned(3 downto 0);                         -- bity danych + parzystosc

    -- ------------------------------------------------------------ PVCC
    constant VOFF_CYC : positive := CLK_HZ / 400;                   -- 2,5 ms
    signal pv_req   : std_logic := '0';
    signal pv_sel   : std_logic_vector(2 downto 0) := (others => '0');
    signal pv_en    : std_logic := '0';
    signal pv_cnt   : integer range 0 to VOFF_CYC := 0;

    -- ------------------------------------------------------------ odczyt
    signal raddr_d  : std_logic_vector(2 downto 0) := (others => '0');
    signal rvalid_d : std_logic := '0';

    function parity_of(v : std_logic_vector) return std_logic is
        variable p : std_logic := '0';
    begin
        for i in v'range loop
            p := p xor v(i);
        end loop;
        return p;
    end function;

    function rev8(v : std_logic_vector(7 downto 0)) return std_logic_vector is
        variable r : std_logic_vector(7 downto 0);
    begin
        for i in 0 to 7 loop
            r(i) := v(7 - i);
        end loop;
        return r;
    end function;

begin

    -- =================================================================== Avalon
    p_regs : process (clk)
    begin
        if rising_edge(clk) then
            clr <= '0';
            if reset = '1' then
                mode   <= (others => '0');
                cpol   <= '0';
                cpha   <= '0';
                lsb    <= '0';
                parity <= "00";
                stop2  <= '0';
                sdel   <= to_unsigned(1, 3);
                div    <= to_unsigned(99, 32);
                hold   <= '0';
                clr    <= '1';
            elsif avs_write = '1' then
                case avs_address is
                    when "001" =>
                        mode   <= unsigned(avs_writedata(2 downto 0));
                        cpol   <= avs_writedata(3);
                        cpha   <= avs_writedata(4);
                        lsb    <= avs_writedata(5);
                        parity <= avs_writedata(7 downto 6);
                        stop2  <= avs_writedata(8);
                        sdel   <= unsigned(avs_writedata(11 downto 9));
                        clr    <= '1';
                    when "010" =>
                        div <= unsigned(avs_writedata);
                    when "101" =>
                        hold <= avs_writedata(0);
                    when others => null;
                end case;
            end if;
        end if;
    end process;

    tx_push <= '1' when avs_write = '1' and avs_address = "011" and tx_cnt(FA) = '0' else '0';
    tx_wd   <= avs_writedata(9 downto 0);
    rx_pop  <= '1' when avs_read = '1' and avs_address = "100" and rx_cnt /= 0 else '0';

    -- odczyt: readLatency 2 (adres i "byl wpis" zapamietane, rx_q = mem(rx_ra) z taktu odczytu)
    p_read : process (clk)
        variable d : std_logic_vector(31 downto 0);
    begin
        if rising_edge(clk) then
            raddr_d  <= avs_address;
            rvalid_d <= rx_pop;
            d := (others => '0');
            case raddr_d is
                when "000" => d := ID;
                when "001" =>
                    d(2 downto 0)  := std_logic_vector(mode);
                    d(3) := cpol;
                    d(4) := cpha;
                    d(5) := lsb;
                    d(7 downto 6)  := parity;
                    d(8) := stop2;
                    d(11 downto 9) := std_logic_vector(sdel);
                when "010" => d := std_logic_vector(div);
                when "100" =>
                    if rvalid_d = '1' then
                        d(8 downto 0) := rx_q;
                        d(31) := '1';
                    end if;
                when "101" =>
                    d(9 downto 0)   := std_logic_vector(tx_cnt);
                    d(19 downto 10) := std_logic_vector(rx_cnt);
                    if tx_cnt /= 0 or spi_busy = '1' or i2c_busy = '1' or ua_busy = '1' then
                        d(20) := '1';
                    end if;
                    d(21) := rx_ovf;
                    d(22) := bus_err;
                    d(23) := tx_cnt(FA);
                    d(24) := hold;
                when "110" =>
                    d(0) := pv_en;
                    d(3 downto 1) := pv_sel;
                when "111" =>
                    d(7 downto 0)  := pin_s2;
                    d(31 downto 8) := std_logic_vector(us_cnt);
                when others => null;
            end case;
            avs_readdata <= d;
        end if;
    end process;

    -- =================================================================== kolejki
    tx_pop  <= spi_pop or i2c_pop or ua_pop;
    rx_push <= spi_push or i2c_push or ua_push;
    rx_wd   <= spi_wd when spi_push = '1' else i2c_wd when i2c_push = '1' else ua_wd;

    rx_we <= '1' when rx_push = '1' and rx_cnt(FA) = '0' else '0';

    u_txmem : entity work.pt_ram
        generic map (AW => FA, DW => 10)
        port map (clk => clk, we => tx_push, waddr => tx_wa, wdata => tx_wd, raddr => tx_ra, q => tx_q);

    u_rxmem : entity work.pt_ram
        generic map (AW => FA, DW => 9)
        port map (clk => clk, we => rx_we, waddr => rx_wa, wdata => rx_wd, raddr => rx_ra, q => rx_q);

    p_fifo : process (clk)
        variable txc, rxc : unsigned(FA downto 0);
    begin
        if rising_edge(clk) then
            if clr = '1' then
                tx_wa <= (others => '0');
                tx_ra <= (others => '0');
                rx_wa <= (others => '0');
                rx_ra <= (others => '0');
                tx_cnt <= (others => '0');
                rx_cnt <= (others => '0');
                rx_ovf <= '0';
                bus_err <= '0';
            else
                txc := tx_cnt;
                rxc := rx_cnt;
                if tx_push = '1' then
                    tx_wa <= tx_wa + 1;
                    txc := txc + 1;
                end if;
                if tx_pop = '1' and tx_cnt /= 0 then
                    tx_ra <= tx_ra + 1;
                    txc := txc - 1;
                end if;
                if rx_push = '1' then
                    if rx_cnt(FA) = '0' then
                        rx_wa <= rx_wa + 1;
                        rxc := rxc + 1;
                    else
                        rx_ovf <= '1';
                    end if;
                end if;
                if rx_pop = '1' then
                    rx_ra <= rx_ra + 1;
                    rxc := rxc - 1;
                end if;
                tx_cnt <= txc;
                rx_cnt <= rxc;
                if i2c_err = '1' then
                    bus_err <= '1';
                end if;
            end if;
        end if;
    end process;

    -- =================================================================== wejscia (2 przerzutniki), licznik us
    p_sync : process (clk)
    begin
        if rising_edge(clk) then
            pin_s1 <= pins_i;
            pin_s2 <= pin_s1;
            if us_pre = US_DIV - 1 then
                us_pre <= 0;
                us_cnt <= us_cnt + 1;
            else
                us_pre <= us_pre + 1;
            end if;
        end if;
    end process;

    go <= '1' when tx_cnt /= 0 and hold = '0' else '0';

    -- =================================================================== SPI master
    -- Polokres h = 0..15: SCK = CPOL xor (h nieparzyste xor CPHA); MOSI zmienia sie na poczatku
    -- polokresow parzystych, MISO probkowane na poczatku nieparzystych (+ sdel taktow, + 2 takty
    -- synchronizatora - linia spi_stb).
    p_spi : process (clk)
        variable nxt : std_logic_vector(7 downto 0);
        variable k   : integer range 0 to 9;
    begin
        if rising_edge(clk) then
            spi_pop  <= '0';
            spi_push <= '0';
            -- probkowanie MISO (niezalezne od stanu, dzialajace takze po koncu bajtu)
            spi_stb <= spi_stb(8 downto 0) & '0';
            k := to_integer(sdel) + 2;
            if spi_stb(k) = '1' then
                spi_rsh <= spi_rsh(6 downto 0) & miso_s;
                spi_rbit <= spi_rbit + 1;
                if spi_rbit = 7 then
                    spi_push <= '1';
                    if lsb = '1' then
                        spi_wd <= '0' & rev8(spi_rsh(6 downto 0) & miso_s);
                    else
                        spi_wd <= '0' & spi_rsh(6 downto 0) & miso_s;
                    end if;
                end if;
            end if;

            if clr = '1' or mode /= 1 then
                spi_st   <= S_IDLE;
                spi_sck  <= cpol;
                spi_cs   <= '1';
                spi_mosi <= '0';
                spi_rbit <= (others => '0');
                spi_stb  <= (others => '0');
            else
                case spi_st is
                    when S_IDLE =>
                        spi_sck <= cpol;
                        if go = '1' then
                            spi_st <= S_LOAD;              -- tx_q gotowe w nastepnym takcie
                        end if;
                    when S_LOAD =>
                        if lsb = '1' then
                            nxt := rev8(tx_q(7 downto 0));
                        else
                            nxt := tx_q(7 downto 0);
                        end if;
                        spi_sh   <= nxt(6 downto 0) & '0';
                        spi_mosi <= nxt(7);
                        spi_last <= tx_q(8);
                        spi_pop  <= '1';
                        spi_h    <= (others => '0');
                        spi_cnt  <= div(15 downto 0);
                        if spi_cs = '1' then
                            spi_cs <= '0';
                            spi_st <= S_CSSU;              -- CS -> pierwsze zbocze: 1 polokres
                        else
                            spi_sck <= cpol xor cpha;
                            spi_st  <= S_BITS;
                        end if;
                    when S_CSSU =>
                        if spi_cnt = 0 then
                            spi_cnt <= div(15 downto 0);
                            spi_sck <= cpol xor cpha;      -- polokres 0
                            spi_st  <= S_BITS;
                        else
                            spi_cnt <= spi_cnt - 1;
                        end if;
                    when S_BITS =>
                        if spi_cnt = 0 then
                            spi_cnt <= div(15 downto 0);
                            if spi_h = 15 then
                                spi_sck <= cpol;
                                if spi_last = '1' then
                                    spi_st <= S_CSH;
                                else
                                    spi_st <= S_WAIT;
                                end if;
                            else
                                spi_h   <= spi_h + 1;
                                spi_sck <= cpol xor (not spi_h(0)) xor cpha;   -- nowy h = h+1
                                if spi_h(0) = '0' then
                                    -- poczatek polokresu nieparzystego: probkowanie
                                    spi_stb(0) <= '1';
                                else
                                    -- poczatek polokresu parzystego: nastepny bit MOSI
                                    spi_mosi <= spi_sh(7);
                                    spi_sh   <= spi_sh(6 downto 0) & '0';
                                end if;
                            end if;
                        else
                            spi_cnt <= spi_cnt - 1;
                        end if;
                    when S_WAIT =>                          -- CS aktywne, czekanie na kolejny bajt
                        if spi_cnt = 0 then
                            if tx_cnt /= 0 and spi_pop = '0' then
                                spi_st <= S_LOAD;
                            end if;
                        else
                            spi_cnt <= spi_cnt - 1;
                        end if;
                    when S_CSH =>                           -- ostatnie zbocze -> CS: 1 polokres
                        if spi_cnt = 0 then
                            spi_cs  <= '1';
                            spi_cnt <= div(15 downto 0);
                            spi_st  <= S_CSOFF;
                        else
                            spi_cnt <= spi_cnt - 1;
                        end if;
                    when S_CSOFF =>                         -- minimalny czas CS = 1
                        if spi_cnt = 0 then
                            spi_st <= S_IDLE;
                        else
                            spi_cnt <= spi_cnt - 1;
                        end if;
                end case;
            end if;
        end if;
    end process;
    spi_busy <= '0' when spi_st = S_IDLE and spi_cs = '1' and spi_stb = "0000000000" else '1';

    -- =================================================================== I2C master
    -- Jednostka czasu U = (DIV + 1) taktow. Bit = 5 U: Q0 (1 U) - na koncu ustaw SDA (SCL = 0),
    -- Q1 (2 U) - na koncu zwolnij SCL (czekaj az SCL = 1: rozciaganie zegara), Q2 (1 U) - na koncu
    -- probkowanie SDA, Q3 (1 U) - na koncu SCL = 0. SCL: 3 U niski / 2 U wysoki (tLOW >= 1,3 us przy
    -- 400 kHz). START: tSU;STA = 2 U, tHD;STA = 1 U. STOP: tSU;STO = 2 U, tBUF = 2 U.
    i2c_u1 <= '0' & div(15 downto 0);
    i2c_u2 <= div(15 downto 0) & '1';
    p_i2c : process (clk)
    begin
        if rising_edge(clk) then
            i2c_pop  <= '0';
            i2c_push <= '0';
            i2c_err  <= '0';
            if clr = '1' or mode /= 2 then
                i2c_st  <= I_IDLE;
                i2c_sda <= '0';
                i2c_scl <= '0';
                i2c_to  <= (others => '0');
            else
                case i2c_st is
                    when I_IDLE =>
                        if go = '1' then
                            i2c_st <= I_LOAD;
                        end if;
                    when I_LOAD =>
                        i2c_pop <= '1';
                        i2c_op  <= tx_q(9 downto 8);
                        i2c_q   <= (others => '0');
                        i2c_cnt <= i2c_u1;
                        i2c_to  <= (others => '0');
                        if tx_q(9 downto 8) = "11" then
                            if tx_q(0) = '1' then
                                i2c_st <= I_START;
                            elsif tx_q(1) = '1' then
                                i2c_st <= I_STOP;
                            else
                                i2c_st <= I_NEXT;
                            end if;
                        else
                            if tx_q(9 downto 8) = "00" then
                                i2c_sh <= tx_q(7 downto 0) & '1';      -- 8 bitow, ACK: zwolnij SDA
                            elsif tx_q(9 downto 8) = "01" then
                                i2c_sh <= x"FF" & '0';                 -- odczyt, ACK = 0
                            else
                                i2c_sh <= x"FF" & '1';                 -- odczyt, NACK = 1
                            end if;
                            i2c_bit <= (others => '0');
                            i2c_st  <= I_BIT;
                        end if;
                    when I_START =>
                        -- Q0: SDA zwolnione, Q1 (2 U): SCL zwolnione (czekaj), Q2 (2 U): SDA = 0, Q3: SCL = 0
                        if i2c_cnt = 0 then
                            i2c_cnt <= i2c_u1;
                            case i2c_q is
                                when "00" => i2c_sda <= '0'; i2c_q <= "01"; i2c_cnt <= i2c_u2;   -- tLOW = 3 U
                                when "01" =>
                                    i2c_scl <= '0';
                                    if scl_s = '1' then
                                        i2c_q   <= "10";
                                        i2c_cnt <= i2c_u2;                     -- tSU;STA
                                    else
                                        i2c_cnt <= (others => '0');
                                        i2c_to  <= i2c_to + 1;
                                        if i2c_to(20) = '1' then
                                            i2c_err <= '1';
                                            i2c_st  <= I_NEXT;
                                        end if;
                                    end if;
                                when "10" => i2c_sda <= '1'; i2c_q <= "11";
                                when others =>
                                    i2c_scl <= '1';
                                    i2c_st  <= I_NEXT;
                            end case;
                        else
                            i2c_cnt <= i2c_cnt - 1;
                        end if;
                    when I_STOP =>
                        -- Q0: SCL = 0, SDA = 0, Q1 (2 U): zwolnij SCL (czekaj), Q2 (2 U): zwolnij SDA,
                        -- Q3 (2 U): czas wolnej magistrali
                        if i2c_cnt = 0 then
                            i2c_cnt <= i2c_u1;
                            case i2c_q is
                                when "00" =>
                                    i2c_scl <= '1';
                                    i2c_sda <= '1';
                                    i2c_q <= "01";
                                    i2c_cnt <= i2c_u2;                         -- tLOW = 3 U
                                when "01" =>
                                    i2c_scl <= '0';
                                    if scl_s = '1' then
                                        i2c_q   <= "10";
                                        i2c_cnt <= i2c_u2;                     -- tSU;STO
                                    else
                                        i2c_cnt <= (others => '0');
                                        i2c_to  <= i2c_to + 1;
                                        if i2c_to(20) = '1' then
                                            i2c_err <= '1';
                                            i2c_sda <= '0';
                                            i2c_st  <= I_NEXT;
                                        end if;
                                    end if;
                                when "10" => i2c_sda <= '0'; i2c_q <= "11"; i2c_cnt <= i2c_u2;   -- tBUF
                                when others =>
                                    i2c_st  <= I_NEXT;
                            end case;
                        else
                            i2c_cnt <= i2c_cnt - 1;
                        end if;
                    when I_BIT =>
                        if i2c_cnt = 0 then
                            i2c_cnt <= i2c_u1;
                            case i2c_q is
                                when "00" =>
                                    i2c_scl <= '1';
                                    i2c_sda <= not i2c_sh(8);
                                    i2c_q   <= "01";
                                    i2c_cnt <= i2c_u2;                         -- Q1 = 2 U: tLOW = 3 U
                                when "10" =>
                                    i2c_rd <= i2c_rd(7 downto 0) & sda_s;
                                    i2c_q  <= "11";
                                when "01" =>
                                    i2c_scl <= '0';
                                    if scl_s = '1' then
                                        i2c_q <= "10";
                                    else
                                        i2c_cnt <= (others => '0');
                                        i2c_to  <= i2c_to + 1;
                                        if i2c_to(20) = '1' then
                                            i2c_err <= '1';
                                            i2c_sda <= '0';
                                            i2c_st  <= I_NEXT;
                                        end if;
                                    end if;
                                when others =>
                                    i2c_scl <= '1';
                                    i2c_q   <= "00";
                                    i2c_to  <= (others => '0');
                                    i2c_sh  <= i2c_sh(7 downto 0) & '1';
                                    if i2c_bit = 8 then
                                        i2c_push <= '1';
                                        if i2c_op = "00" then
                                            i2c_wd <= i2c_rd(0) & x"00";          -- [8] = NACK
                                        else
                                            i2c_wd <= '0' & i2c_rd(8 downto 1);  -- odebrany bajt
                                        end if;
                                        i2c_st <= I_NEXT;
                                    else
                                        i2c_bit <= i2c_bit + 1;
                                    end if;
                            end case;
                        else
                            i2c_cnt <= i2c_cnt - 1;
                        end if;
                    when I_NEXT =>
                        if i2c_err = '1' then
                            i2c_scl <= '0';
                            i2c_sda <= '0';
                        end if;
                        if tx_cnt /= 0 and i2c_pop = '0' then
                            i2c_st <= I_LOAD;
                        elsif tx_cnt = 0 then
                            i2c_st <= I_IDLE;
                        end if;
                end case;
            end if;
        end if;
    end process;
    i2c_busy <= '0' when i2c_st = I_IDLE else '1';

    -- =================================================================== UART
    -- Akumulator fazy: tick = 16 x baud. TX: ramka w ua_tsh (LSB pierwszy), 16 tickow na bit.
    -- RX: zbocze opadajace -> 8 tickow -> sprawdzenie bitu startu -> bity co 16 tickow.
    ua_nbits <= to_unsigned(8, 4) when parity = "00" else to_unsigned(9, 4);

    p_ua : process (clk)
        variable sum : unsigned(32 downto 0);
        variable fr  : std_logic_vector(11 downto 0);
        variable p   : std_logic;
    begin
        if rising_edge(clk) then
            ua_pop  <= '0';
            ua_push <= '0';
            sum := ('0' & ua_acc) + ('0' & div);
            ua_acc  <= sum(31 downto 0);
            ua_tick <= sum(32);
            if clr = '1' or mode /= 3 then
                ua_tst <= T_IDLE;
                ua_rst <= R_IDLE;
                ua_txo <= '1';
                ua_acc <= (others => '0');
            else
                -- ---------------- nadajnik
                case ua_tst is
                    when T_IDLE =>
                        ua_txo <= '1';
                        if go = '1' then
                            ua_tst <= T_LOAD;
                        end if;
                    when T_LOAD =>
                        ua_pop <= '1';
                        p := parity_of(tx_q(7 downto 0));
                        if parity = "10" then
                            p := not p;
                        end if;
                        -- ramka od LSB: start, 8 danych, [parzystosc], stop(y); reszta = 1
                        fr := (others => '1');
                        fr(0) := '0';
                        fr(8 downto 1) := tx_q(7 downto 0);
                        if parity /= "00" then
                            fr(9) := p;
                        end if;
                        ua_tsh <= fr;
                        -- liczba bitow: 1 + 8 + par + stop(1/2)
                        if parity = "00" then
                            if stop2 = '1' then ua_tn <= to_unsigned(11, 4); else ua_tn <= to_unsigned(10, 4); end if;
                        else
                            if stop2 = '1' then ua_tn <= to_unsigned(12, 4); else ua_tn <= to_unsigned(11, 4); end if;
                        end if;
                        ua_tt  <= (others => '0');
                        ua_txo <= '0';
                        ua_tst <= T_SEND;
                    when T_SEND =>
                        if ua_tick = '1' then
                            if ua_tt = 15 then
                                ua_tt <= (others => '0');
                                if ua_tn = 1 then
                                    ua_txo <= '1';
                                    ua_tst <= T_IDLE;
                                else
                                    ua_tn  <= ua_tn - 1;
                                    ua_tsh <= '1' & ua_tsh(11 downto 1);
                                    ua_txo <= ua_tsh(1);
                                end if;
                            else
                                ua_tt <= ua_tt + 1;
                            end if;
                        end if;
                end case;

                -- ---------------- odbiornik
                case ua_rst is
                    when R_IDLE =>
                        if rx_s = '0' then
                            ua_rt  <= (others => '0');
                            ua_rst <= R_START;
                        end if;
                    when R_START =>
                        if ua_tick = '1' then
                            if ua_rt = 7 then
                                if rx_s = '0' then
                                    ua_rt  <= (others => '0');
                                    ua_rn  <= (others => '0');
                                    ua_rst <= R_BITS;
                                else
                                    ua_rst <= R_IDLE;          -- zaklocenie
                                end if;
                            else
                                ua_rt <= ua_rt + 1;
                            end if;
                        end if;
                    when R_BITS =>
                        if ua_tick = '1' then
                            if ua_rt = 15 then
                                ua_rt <= (others => '0');
                                if ua_rn = ua_nbits then
                                    -- bit stopu
                                    ua_push <= '1';
                                    p := parity_of(ua_rsh(9 downto 2));
                                    if parity = "10" then
                                        p := not p;
                                    end if;
                                    if parity = "00" then
                                        ua_wd <= (not rx_s) & ua_rsh(10 downto 3);
                                    else
                                        ua_wd <= ((not rx_s) or (p xor ua_rsh(10))) & ua_rsh(9 downto 2);
                                    end if;
                                    ua_rst <= R_IDLE;
                                else
                                    ua_rsh <= rx_s & ua_rsh(10 downto 1);
                                    ua_rn  <= ua_rn + 1;
                                end if;
                            else
                                ua_rt <= ua_rt + 1;
                            end if;
                        end if;
                end case;
            end if;
        end if;
    end process;
    ua_busy <= '0' when ua_tst = T_IDLE else '1';

    -- =================================================================== PVCC
    p_pvcc : process (clk)
    begin
        if rising_edge(clk) then
            if reset = '1' then
                pv_req <= '0';
                pv_sel <= (others => '0');
                pv_en  <= '0';
                pv_cnt <= 0;
            else
                if avs_write = '1' and avs_address = "110" then
                    pv_req <= avs_writedata(0);
                    pv_sel <= avs_writedata(3 downto 1);
                    pv_en  <= '0';
                    pv_cnt <= VOFF_CYC;
                elsif pv_cnt /= 0 then
                    pv_cnt <= pv_cnt - 1;
                    if pv_cnt = 1 then
                        pv_en <= pv_req;
                    end if;
                end if;
            end if;
        end if;
    end process;
    pvcc_ctrl <= pv_sel & pv_en;

    -- =================================================================== wyjscia
    p_out : process (clk)
    begin
        if rising_edge(clk) then
            if reset = '1' then
                dir_spi  <= '0';
                dir_miso <= '0';
                dir_tx   <= '0';
                sda_drv  <= '0';
                scl_drv  <= '0';
                sck_o    <= '0';
                mosi_o   <= '0';
                cs_o     <= '1';
                tx_o     <= '1';
            else
                if mode = 1 then dir_spi <= '1'; else dir_spi <= '0'; end if;
                dir_miso <= '0';
                if mode = 3 then dir_tx <= '1'; else dir_tx <= '0'; end if;
                if mode = 2 then
                    sda_drv <= i2c_sda;
                    scl_drv <= i2c_scl;
                else
                    sda_drv <= '0';
                    scl_drv <= '0';
                end if;
                sck_o  <= spi_sck;
                mosi_o <= spi_mosi;
                cs_o   <= spi_cs;
                tx_o   <= ua_txo;
            end if;
        end if;
    end process;
    miso_o <= '0';

end architecture;
