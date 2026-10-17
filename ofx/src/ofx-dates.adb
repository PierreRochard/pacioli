with Ofx.Chars; use Ofx.Chars;

package body Ofx.Dates with SPARK_Mode is

   Minutes_Per_Day : constant := 24 * 60;

   procedure Previous_Day (T : in out Timestamp; Ok : out Boolean)
   with Pre => Valid (T), Post => (if Ok then Valid (T))
   is
   begin
      Ok := True;
      if T.Day > 1 then
         T.Day := T.Day - 1;
      elsif T.Month > 1 then
         T.Month := T.Month - 1;
         T.Day := Days_In_Month (T.Year, T.Month);
      elsif T.Year > Year_Number'First then
         T.Year := T.Year - 1;
         T.Month := 12;
         T.Day := 31;
      else
         Ok := False;
      end if;
   end Previous_Day;

   procedure Next_Day (T : in out Timestamp; Ok : out Boolean)
   with Pre => Valid (T), Post => (if Ok then Valid (T))
   is
   begin
      Ok := True;
      if T.Day < Days_In_Month (T.Year, T.Month) then
         T.Day := T.Day + 1;
      elsif T.Month < 12 then
         T.Month := T.Month + 1;
         T.Day := 1;
      elsif T.Year < Year_Number'Last then
         T.Year := T.Year + 1;
         T.Month := 1;
         T.Day := 1;
      else
         Ok := False;
      end if;
   end Next_Day;

   procedure To_Utc
     (Local  : Timestamp;
      Offset : Integer;
      Result : out Timestamp;
      Ok     : out Boolean)
   is
      Minutes : Integer := Local.Hour * 60 + Local.Minute - Offset;
   begin
      Result := Local;
      Ok := True;
      if Minutes < 0 then
         Minutes := Minutes + Minutes_Per_Day;
         Previous_Day (Result, Ok);
      elsif Minutes >= Minutes_Per_Day then
         Minutes := Minutes - Minutes_Per_Day;
         Next_Day (Result, Ok);
      end if;
      pragma Assert (Minutes in 0 .. Minutes_Per_Day - 1);
      Result.Hour := Minutes / 60;
      Result.Minute := Minutes mod 60;
   end To_Utc;

   procedure Parse (Text : String; S : Span; Result : out Timestamp; Ok : out Boolean) is
      Position : Positive;
      First    : Positive;
      Count    : Natural;
      Local    : Timestamp;
      Year, Month, Day : Natural;
      Hour, Minute, Second : Natural := 0;
      Fraction : array (1 .. 6) of Natural := [others => 0];
      Negative : Boolean := False;
      Hours    : Natural := 0;
      Minutes  : Natural := 0;
      Offset   : Integer := 0;

      function D (Index : Positive) return Natural is (Digit (Text (Index)))
      with Pre => Index in Text'Range and then Is_Digit (Text (Index));
   begin
      Result := (others => <>);
      Ok := False;
      if Is_Empty (S) then
         return;
      end if;
      First := S.First;

      --  The run of digits: 8, 12, or 14 of them.
      Position := First;
      while Position <= S.Last and then Is_Digit (Text (Position)) loop
         pragma Loop_Invariant (Position in First .. S.Last);
         pragma Loop_Invariant (for all I in First .. Position => Is_Digit (Text (I)));
         pragma Loop_Variant (Increases => Position);
         Position := Position + 1;
      end loop;
      Count := Position - First;
      if Count not in 8 | 12 | 14 then
         return;
      end if;
      pragma Assert (for all I in First .. First + Count - 1 => Is_Digit (Text (I)));

      Year := D (First) * 1000 + D (First + 1) * 100 + D (First + 2) * 10 + D (First + 3);
      Month := D (First + 4) * 10 + D (First + 5);
      Day := D (First + 6) * 10 + D (First + 7);
      if Count >= 12 then
         Hour := D (First + 8) * 10 + D (First + 9);
         Minute := D (First + 10) * 10 + D (First + 11);
      end if;
      if Count = 14 then
         Second := D (First + 12) * 10 + D (First + 13);
      end if;

      --  Fractional seconds, 1 to 6 digits, only after HHMMSS.
      if Count = 14 and then Position <= S.Last and then Text (Position) = '.' then
         Position := Position + 1;
         Count := 0;
         while Position <= S.Last and then Is_Digit (Text (Position)) loop
            pragma Loop_Invariant (Position in S.First .. S.Last);
            pragma Loop_Invariant (Count <= 6);
            pragma Loop_Invariant (for all I in Fraction'Range => Fraction (I) <= 9);
            pragma Loop_Variant (Increases => Position);
            if Count = 6 then
               return;
            end if;
            Count := Count + 1;
            Fraction (Count) := D (Position);
            Position := Position + 1;
         end loop;
         if Count = 0 then
            return;
         end if;
      end if;

      --  [offset] or [offset:name], the offset in hours with an optional
      --  fraction that must come to whole minutes.
      if Position <= S.Last then
         if Text (Position) /= '[' or else Text (S.Last) /= ']' or else Position = S.Last then
            return;
         end if;
         Position := Position + 1;
         if Text (Position) in '+' | '-' then
            Negative := Text (Position) = '-';
            Position := Position + 1;
         end if;
         Count := 0;
         while Position < S.Last and then Is_Digit (Text (Position)) loop
            pragma Loop_Invariant (Position in S.First .. S.Last - 1);
            pragma Loop_Invariant
              ((Count = 0 and then Hours = 0) or else (Count = 1 and then Hours <= 9)
               or else (Count = 2 and then Hours <= 99));
            pragma Loop_Variant (Increases => Position);
            if Count = 2 then
               return;
            end if;
            Hours := Hours * 10 + D (Position);
            Count := Count + 1;
            Position := Position + 1;
         end loop;
         if Count = 0 then
            return;
         end if;
         if Position < S.Last and then Text (Position) = '.' then
            Position := Position + 1;
            --  Hundredths of an hour; 36 seconds each, so whole minutes need
            --  a multiple of 5 hundredths (.25, .5, .75).
            Count := 0;
            while Position < S.Last and then Is_Digit (Text (Position)) loop
               pragma Loop_Invariant (Position in S.First .. S.Last - 1);
               pragma Loop_Invariant
                 ((Count = 0 and then Minutes = 0) or else (Count = 1 and then Minutes <= 9)
                  or else (Count = 2 and then Minutes <= 99));
               pragma Loop_Variant (Increases => Position);
               if Count = 2 then
                  return;
               end if;
               Minutes := Minutes * 10 + D (Position);
               Count := Count + 1;
               Position := Position + 1;
            end loop;
            if Count = 0 then
               return;
            elsif Count = 1 then
               Minutes := Minutes * 10;
            end if;
            if Minutes mod 5 /= 0 then
               return;
            end if;
            Minutes := Minutes * 60 / 100;
         end if;
         if Position < S.Last and then Text (Position) = ':' then
            Position := Position + 1;
            Count := 0;
            while Position < S.Last loop
               pragma Loop_Invariant (Position in S.First .. S.Last - 1);
               pragma Loop_Invariant (Count <= 16);
               pragma Loop_Variant (Increases => Position);
               if Count = 16
                 or else not (Is_Alphanumeric (Text (Position)) or else Text (Position) = '_')
               then
                  return;
               end if;
               Count := Count + 1;
               Position := Position + 1;
            end loop;
         end if;
         if Position /= S.Last then
            return;
         end if;
         Offset := Hours * 60 + Minutes;
         if Offset > Max_Offset then
            return;
         end if;
         if Negative then
            Offset := -Offset;
         end if;
      end if;

      if Year not in Year_Number or else Month not in Month_Number
        or else Day not in Day_Number or else Hour not in Hour_Number
        or else Minute not in Minute_Number or else Second not in Second_Number
      then
         return;
      end if;
      if Day > Days_In_Month (Year, Month) then
         return;
      end if;

      Local := (Year => Year, Month => Month, Day => Day, Hour => Hour,
                Minute => Minute, Second => Second,
                Microsecond => Fraction (1) * 100_000 + Fraction (2) * 10_000
                               + Fraction (3) * 1_000 + Fraction (4) * 100
                               + Fraction (5) * 10 + Fraction (6));
      To_Utc (Local, Offset, Result, Ok);
   end Parse;

end Ofx.Dates;
