with Ofx.Dates;
with Ofx.Decimals;
with Ofx.Values; use Ofx.Values;

package body Ofx.Emit with SPARK_Mode is

   use Ofx.Json;
   use Ofx.Model;

   procedure Put_Raw (W : in out Writer; Text : String; S : Span)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Within (S, Text)
   is
   begin
      for I in S.First .. S.Last loop
         Put (W, Text (I));
      end loop;
   end Put_Raw;

   procedure Put_Date (W : in out Writer; T : Dates.Timestamp)
   with Global => (In_Out => Ofx.IO.Output_State)
   is
   begin
      Put (W, '"');
      Put_Number (W, T.Year, 4);
      Put (W, '-');
      Put_Number (W, T.Month, 2);
      Put (W, '-');
      Put_Number (W, T.Day, 2);
      Put (W, 'T');
      Put_Number (W, T.Hour, 2);
      Put (W, ':');
      Put_Number (W, T.Minute, 2);
      Put (W, ':');
      Put_Number (W, T.Second, 2);
      if T.Microsecond /= 0 then
         Put (W, '.');
         Put_Number (W, T.Microsecond, 6);
      end if;
      Put (W, "Z""");
   end Put_Date;

   procedure Put_Amount (W : in out Writer; Text : String; D : Decimals.Decimal)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Within (D.Whole, Text) and then Within (D.Fraction, Text)
   is
   begin
      Put (W, '"');
      if D.Negative then
         Put (W, '-');
      end if;
      if Is_Empty (D.Whole) then
         Put (W, '0');
      else
         Put_Raw (W, Text, D.Whole);
      end if;
      if not Is_Empty (D.Fraction) then
         Put (W, '.');
         Put_Raw (W, Text, D.Fraction);
      end if;
      Put (W, '"');
   end Put_Amount;

   --  A stored element: its typed JSON value, or null.
   procedure Put_Field
     (W        : in out Writer;
      Text     : String;
      F        : Field;
      R        : Rule;
      Encoding : Charset)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Text'First = 1 and then Text'Last <= Max_Input
   is
      S      : constant Span := F.Value;
      Date   : Dates.Timestamp;
      Amount : Decimals.Decimal;
      Ok     : Boolean;
      First  : Positive;
   begin
      if not F.Seen or else Is_Empty (S) or else not Within (S, Text) then
         Put (W, "null");
         return;
      end if;
      case R.Kind is
         when Text_Value =>
            Put_String (W, Text, S, Encoding, References => True);
         when Date_Value =>
            Dates.Parse (Text, S, Date, Ok);
            if Ok then
               Put_Date (W, Date);
            else
               Put (W, "null");
            end if;
         when Amount_Value =>
            Decimals.Parse (Text, S, Amount, Ok);
            if Ok then
               Put_Amount (W, Text, Amount);
            else
               Put (W, "null");
            end if;
         when Number_Value =>
            --  JSON numbers carry no leading zeros.
            First := S.First;
            while First < S.Last and then Text (First) = '0' loop
               pragma Loop_Invariant (First in S.First .. S.Last - 1);
               pragma Loop_Variant (Increases => First);
               First := First + 1;
            end loop;
            Put_Raw (W, Text, (First => First, Last => S.Last));
         when others =>
            --  Enumerations and ISO codes: checked capital letters.
            Put (W, '"');
            Put_Raw (W, Text, S);
            Put (W, '"');
      end case;
   end Put_Field;

   procedure Put_Key (W : in out Writer; Key : String; First : Boolean := False)
   with Global => (In_Out => Ofx.IO.Output_State)
   is
   begin
      if not First then
         Put (W, ',');
      end if;
      Put (W, '"');
      Put (W, Key);
      Put (W, """:");
   end Put_Key;

   procedure Put_Status (W : in out Writer; Text : String; S : Status_Record; Encoding : Charset)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Text'First = 1 and then Text'Last <= Max_Input
   is
   begin
      if not S.Seen then
         Put (W, "null");
         return;
      end if;
      --  MESSAGE is checked but not written: providers put account and
      --  credential details there, and Pacioli keeps only status codes.
      Put (W, '{');
      Put_Key (W, "code", First => True);
      Put_Field (W, Text, S.Fields (Code), Status_Rule (Code), Encoding);
      Put_Key (W, "severity");
      Put_Field (W, Text, S.Fields (Severity), Status_Rule (Severity), Encoding);
      Put (W, '}');
   end Put_Status;

   function Transaction_Key (F : Transaction_Field) return String is
     (case F is
         when Trntype       => "trntype",
         when Dtposted      => "dtposted",
         when Dtuser        => "dtuser",
         when Dtavail       => "dtavail",
         when Trnamt        => "trnamt",
         when Fitid         => "fitid",
         when Correctfitid  => "correctfitid",
         when Correctaction => "correctaction",
         when Srvrtid       => "srvrtid",
         when Checknum      => "checknum",
         when Refnum        => "refnum",
         when Sic           => "sic",
         when Payeeid       => "payeeid",
         when Name          => "name",
         when Memo          => "memo",
         when Inv401ksource => "inv401ksource",
         when Currate       => "currate",
         when Cursym        => "cursym");

   procedure Put_Transaction
     (W        : in out Writer;
      Text     : String;
      T        : Transaction_Record;
      Encoding : Charset)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Text'First = 1 and then Text'Last <= Max_Input
   is
   begin
      Put (W, '{');
      for F in Trntype .. Inv401ksource loop
         Put_Key (W, Transaction_Key (F), First => F = Trntype);
         Put_Field (W, Text, T.Fields (F), Transaction_Rule (F), Encoding);
      end loop;
      Put_Key (W, "currency");
      if T.Currency = No_Currency then
         Put (W, "null");
      else
         Put (W, (if T.Currency = Currency then "{""kind"":""CURRENCY"""
                  else "{""kind"":""ORIGCURRENCY"""));
         Put_Key (W, "currate");
         Put_Field (W, Text, T.Fields (Currate), Transaction_Rule (Currate), Encoding);
         Put_Key (W, "cursym");
         Put_Field (W, Text, T.Fields (Cursym), Transaction_Rule (Cursym), Encoding);
         Put (W, '}');
      end if;
      Put (W, '}');
   end Put_Transaction;

   procedure Put_Statement_Field
     (W        : in out Writer;
      Text     : String;
      S        : Statement_Record;
      F        : Statement_Field;
      Key      : String;
      Encoding : Charset;
      First    : Boolean := False)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Text'First = 1 and then Text'Last <= Max_Input
   is
   begin
      Put_Key (W, Key, First);
      Put_Field (W, Text, S.Fields (F), Statement_Rule (F), Encoding);
   end Put_Statement_Field;

   procedure Put_Statement
     (W        : in out Writer;
      Text     : String;
      Doc      : Model.Document;
      S        : Statement_Record;
      Encoding : Charset)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Text'First = 1 and then Text'Last <= Max_Input
   is
      Last : constant Natural := Natural'Min (S.Last_Transaction, Doc.Transaction_Count);
   begin
      Put (W, (if S.Kind = Bank then "{""type"":""bank""" else "{""type"":""creditcard"""));
      Put_Statement_Field (W, Text, S, Trnuid, "trnuid", Encoding);
      Put_Key (W, "status");
      Put_Status (W, Text, S.Status, Encoding);
      Put_Key (W, "statement");
      if not S.Has_Response then
         Put (W, "null}");
         return;
      end if;
      Put (W, '{');
      Put_Statement_Field (W, Text, S, Curdef, "curdef", Encoding, First => True);

      Put_Key (W, "account");
      if not S.Has_Account then
         Put (W, "null");
      elsif S.Kind = Bank then
         Put (W, '{');
         Put_Statement_Field (W, Text, S, Bankid, "bankid", Encoding, First => True);
         Put_Statement_Field (W, Text, S, Branchid, "branchid", Encoding);
         Put_Statement_Field (W, Text, S, Acctid, "acctid", Encoding);
         Put_Statement_Field (W, Text, S, Accttype, "accttype", Encoding);
         Put_Statement_Field (W, Text, S, Acctkey, "acctkey", Encoding);
         Put (W, '}');
      else
         Put (W, '{');
         Put_Statement_Field (W, Text, S, Acctid, "acctid", Encoding, First => True);
         Put_Statement_Field (W, Text, S, Acctkey, "acctkey", Encoding);
         Put (W, '}');
      end if;

      Put_Key (W, "banktranlist");
      if not S.Has_Tranlist then
         Put (W, "null");
      else
         Put (W, '{');
         Put_Statement_Field (W, Text, S, Dtstart, "dtstart", Encoding, First => True);
         Put_Statement_Field (W, Text, S, Dtend, "dtend", Encoding);
         Put_Key (W, "transactions");
         Put (W, '[');
         for I in S.First_Transaction .. Last loop
            if I > S.First_Transaction then
               Put (W, ',');
            end if;
            Put_Transaction (W, Text, Doc.Transactions (I), Encoding);
         end loop;
         Put (W, "]}");
      end if;

      Put_Key (W, "ledgerbal");
      if not S.Has_Ledgerbal then
         Put (W, "null");
      else
         Put (W, '{');
         Put_Statement_Field (W, Text, S, Ledger_Balamt, "balamt", Encoding, First => True);
         Put_Statement_Field (W, Text, S, Ledger_Dtasof, "dtasof", Encoding);
         Put (W, '}');
      end if;

      Put_Key (W, "availbal");
      if not S.Has_Availbal then
         Put (W, "null");
      else
         Put (W, '{');
         Put_Statement_Field (W, Text, S, Avail_Balamt, "balamt", Encoding, First => True);
         Put_Statement_Field (W, Text, S, Avail_Dtasof, "dtasof", Encoding);
         Put (W, '}');
      end if;
      Put (W, "}}");
   end Put_Statement;

   procedure Put_Header (W : in out Writer; Text : String; H : Header_Record)
   with Global => (In_Out => Ofx.IO.Output_State)
   is
      procedure Put_Value (Key : String; S : Span) is
      begin
         Put_Key (W, Key);
         if Is_Empty (S) or else not Within (S, Text) then
            Put (W, "null");
         else
            --  Header values are letters, digits, '-', '_' and '.'.
            Put (W, '"');
            Put_Raw (W, Text, S);
            Put (W, '"');
         end if;
      end Put_Value;
   begin
      Put (W, '{');
      Put_Key (W, "major", First => True);
      Put_Number (W, H.Major);
      Put_Key (W, "version");
      Put_Number (W, H.Version);
      Put_Value ("security", H.Security);
      Put_Value ("encoding", H.Encoding);
      Put_Value ("charset", H.Charset);
      Put_Value ("compression", H.Compression);
      Put_Value ("oldfileuid", H.Old_File);
      Put_Value ("newfileuid", H.New_File);
      Put (W, '}');
   end Put_Header;

   procedure Document (W : in out Json.Writer; Text : String; Doc : Model.Document) is
      Encoding : constant Charset := Doc.Header.Decoding;
   begin
      Put (W, "{""format"":""pacioli-ofx/1""");
      Put_Key (W, "header");
      Put_Header (W, Text, Doc.Header);

      Put_Key (W, "signon");
      if not Doc.Signon.Seen then
         Put (W, "null");
      else
         Put (W, '{');
         Put_Key (W, "status", First => True);
         Put_Status (W, Text, Doc.Signon.Status, Encoding);
         Put_Key (W, "dtserver");
         Put_Field (W, Text, Doc.Signon.Fields (Dtserver), Signon_Rule (Dtserver), Encoding);
         Put_Key (W, "language");
         Put_Field (W, Text, Doc.Signon.Fields (Language), Signon_Rule (Language), Encoding);
         Put_Key (W, "fi");
         if not Doc.Signon.Has_Fi then
            Put (W, "null");
         else
            Put (W, '{');
            Put_Key (W, "org", First => True);
            Put_Field (W, Text, Doc.Signon.Fields (Org), Signon_Rule (Org), Encoding);
            Put_Key (W, "fid");
            Put_Field (W, Text, Doc.Signon.Fields (Fid), Signon_Rule (Fid), Encoding);
            Put (W, '}');
         end if;
         Put (W, '}');
      end if;

      Put_Key (W, "statements");
      Put (W, '[');
      for I in 1 .. Doc.Statement_Count loop
         if I > 1 then
            Put (W, ',');
         end if;
         Put_Statement (W, Text, Doc, Doc.Statements (I), Encoding);
      end loop;
      Put (W, "]}");
      Put (W, ASCII.LF);
   end Document;

   function Kind_Name (K : Parser.Error_Kind) return String is
     (case K is
         when Parser.No_Error            => "none",
         when Parser.Header_Invalid      => "header_invalid",
         when Parser.Syntax_Invalid      => "syntax_invalid",
         when Parser.Element_Unsupported => "element_unsupported",
         when Parser.Element_Duplicate   => "element_duplicate",
         when Parser.Value_Invalid       => "value_invalid",
         when Parser.Limit_Exceeded      => "limit_exceeded");

   procedure Error (W : in out Json.Writer; Text : String; E : Parser.Error_Record) is
   begin
      Put (W, "{""error"":""");
      Put (W, Kind_Name (E.Kind));
      Put (W, """,""path"":""");
      for I in 1 .. E.Depth loop
         if I > 1 then
            Put (W, '/');
         end if;
         --  Element names: capital letters, digits and '.'.
         Put_Raw (W, Text, E.Path (I));
      end loop;
      Put (W, """,""offset"":");
      Put_Number (W, (if E.Position = 0 then 0 else E.Position - 1));
      Put (W, '}');
      Put (W, ASCII.LF);
   end Error;

end Ofx.Emit;
