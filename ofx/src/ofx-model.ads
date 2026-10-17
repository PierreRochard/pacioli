--  What a parse keeps: for each recognized element, whether it occurred and
--  the span of its value. Values are validated when stored and converted only
--  when written out. Storage is static and bounded.

package Ofx.Model with SPARK_Mode is

   Max_Statements   : constant := 32;
   Max_Transactions : constant := 100_000;

   type Field is record
      Seen  : Boolean := False;   --  the element occurred, possibly empty
      Value : Span := No_Span;    --  its value without edge white space
   end record;

   type Header_Record is record
      Major       : Positive range 1 .. 2 := 1;
      Version     : Natural range 0 .. 999 := 0;
      Security    : Span := No_Span;
      Encoding    : Span := No_Span;
      Charset     : Span := No_Span;   --  OFX 1 only
      Compression : Span := No_Span;   --  OFX 1 only
      Old_File    : Span := No_Span;
      New_File    : Span := No_Span;
      Decoding    : Ofx.Charset := Us_Ascii;
      Body_Start  : Positive := 1;
   end record;

   type Status_Field is (Code, Severity, Message);
   type Status_Fields is array (Status_Field) of Field;

   type Status_Record is record
      Seen   : Boolean := False;
      Fields : Status_Fields;
   end record;

   type Signon_Field is (Dtserver, Language, Org, Fid);
   type Signon_Fields is array (Signon_Field) of Field;

   type Signon_Record is record
      Seen   : Boolean := False;   --  SONRS
      Has_Fi : Boolean := False;
      Status : Status_Record;
      Fields : Signon_Fields;
   end record;

   type Statement_Kind is (Bank, Card);

   type Statement_Field is
     (Trnuid, Curdef, Bankid, Branchid, Acctid, Accttype, Acctkey, Dtstart, Dtend,
      Ledger_Balamt, Ledger_Dtasof, Avail_Balamt, Avail_Dtasof);
   type Statement_Fields is array (Statement_Field) of Field;

   --  A STMTTRNRS or CCSTMTTRNRS; Has_Response when it holds a statement.
   type Statement_Record is record
      Kind              : Statement_Kind := Bank;
      Status            : Status_Record;
      Has_Response      : Boolean := False;
      Has_Account       : Boolean := False;
      Has_Tranlist      : Boolean := False;
      Has_Ledgerbal     : Boolean := False;
      Has_Availbal      : Boolean := False;
      Fields            : Statement_Fields;
      First_Transaction : Positive := 1;
      Last_Transaction  : Natural := 0;
   end record;

   type Transaction_Field is
     (Trntype, Dtposted, Dtuser, Dtavail, Trnamt, Fitid, Correctfitid, Correctaction,
      Srvrtid, Checknum, Refnum, Sic, Payeeid, Name, Memo, Inv401ksource, Currate, Cursym);
   type Transaction_Fields is array (Transaction_Field) of Field;

   type Currency_Kind is (No_Currency, Currency, Original_Currency);

   type Transaction_Record is record
      Fields   : Transaction_Fields;
      Currency : Currency_Kind := No_Currency;
   end record;

   type Statement_Array is array (1 .. Max_Statements) of Statement_Record;
   type Transaction_Array is array (1 .. Max_Transactions) of Transaction_Record;

   type Document is record
      Header            : Header_Record;
      Signon_Messages   : Boolean := False;
      Bank_Messages     : Boolean := False;
      Card_Messages     : Boolean := False;
      Signon            : Signon_Record;
      Statement_Count   : Natural range 0 .. Max_Statements := 0;
      Statements        : Statement_Array;
      Transaction_Count : Natural range 0 .. Max_Transactions := 0;
      Transactions      : Transaction_Array;
   end record;

end Ofx.Model;
